import tempfile
import unittest
from pathlib import Path

import torch
from tensordict import TensorDict

from driver import rl_dreamer


class CompactReplay(unittest.TestCase):
    def test_sampled_actions_are_the_stored_one_hots_one_step_back(self):
        buf = rl_dreamer._make_buffer(200, "cpu")
        stored = {}
        for t in range(120):
            a = torch.zeros(1, rl_dreamer.ACT_DIM)
            if t % 10:   # every tenth step is a reset step with a zeroed action
                a[0, [t % 3, 3 + t % 2, 5 + (t // 2) % 2, 7 + (t * 37) % 72]] = 1
            stored[t] = a[0]
            buf.add_transition(TensorDict({
                "state": torch.full((1, 1), float(t)), "action": a, "reward": torch.zeros(1, 1),
                "is_first": torch.zeros(1, 1, dtype=torch.bool), "stoch": torch.randn(1, 32, 16),
                "deter": torch.randn(1, 2048), "episode": torch.zeros(1, dtype=torch.int32)}, batch_size=(1,)))
        data, _, (stoch, deter) = buf.sample()
        self.assertEqual(data["action"].shape, (rl_dreamer.BATCH, rl_dreamer.LENGTH, rl_dreamer.ACT_DIM))
        self.assertEqual((stoch.dtype, deter.dtype), (torch.float32, torch.float32))
        for s, a in zip(data["state"].flatten().tolist(), data["action"].reshape(-1, rl_dreamer.ACT_DIM)):
            self.assertTrue(torch.equal(a, stored[int(s) - 1]))


class ReplayFile(unittest.TestCase):
    N = 2

    def _step(self, t):
        a = torch.zeros(self.N, rl_dreamer.ACT_DIM)
        for e in range(self.N):
            a[e, [(t + e) % 3, 3 + t % 2, 5 + e, 7 + (t * 37 + e) % 72]] = 1
        g = torch.Generator().manual_seed(t)
        return TensorDict({
            "state": torch.tensor([[10.0 * t + e] for e in range(self.N)]), "action": a,
            "reward": torch.zeros(self.N, 1), "is_first": torch.zeros(self.N, 1, dtype=torch.bool),
            "stoch": torch.randn(self.N, 32, 16, generator=g), "deter": torch.randn(self.N, 2048, generator=g),
            "episode": torch.arange(self.N, dtype=torch.int32)}, batch_size=(self.N,))

    def test_round_trip_keeps_time_order_and_appends_after_it(self):
        old = rl_dreamer._make_buffer(80 * self.N, "cpu")
        for t in range(90):   # wraps: rows t = 10..89 remain
            old.add_transition(self._step(t))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replay.pt"
            rl_dreamer._save_replay(old, path, 1234)
            r = rl_dreamer._read_replay(path)
        self.assertEqual((r["timesteps"], r["shape"]), (1234, [80, self.N]))
        new = rl_dreamer._make_buffer(200 * self.N, "cpu")
        rl_dreamer._fill_replay(new, r["data"])
        for t in range(90, 110):
            new.add_transition(self._step(t))

        st = new._buffer.storage
        self.assertEqual(st._len_along_dim0, 100)
        rows = st._storage[:100]
        for i, t in enumerate(range(10, 110)):
            want = self._step(t)
            for e in range(self.N):
                self.assertEqual(float(rows["state"][i, e, 0]), 10.0 * t + e)
                self.assertEqual(rows["action"][i, e].tolist(),
                                 [int(x.argmax()) + 1 for x in torch.split(want["action"][e], rl_dreamer.ACTION_NVEC)])
                self.assertEqual(rows["action"].dtype, torch.int16)
                self.assertTrue(torch.equal(rows["stoch"][i, e], want["stoch"][e].half()))
                self.assertTrue(torch.equal(rows["deter"][i, e], want["deter"][e].half()))

        data, _, _ = new.sample()   # sequences run through the load seam in time order
        state = data["state"][..., 0]
        self.assertTrue(torch.equal(state[:, 1:] - state[:, :-1], torch.full_like(state[:, 1:], 10.0)))
        for s, a in zip(state.flatten().tolist(), data["action"].reshape(-1, rl_dreamer.ACT_DIM)):
            t, e = divmod(int(s), 10)
            self.assertTrue(torch.equal(a, self._step(t - 1)["action"][e]))

    def test_file_holds_only_the_filled_rows(self):
        buf = rl_dreamer._make_buffer(1000 * self.N, "cpu")
        for t in range(10):
            buf.add_transition(self._step(t))
        filled = sum(v.nbytes for v in buf._buffer.storage._storage[:10].values())
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replay.pt"
            rl_dreamer._save_replay(buf, path, 20)
            self.assertLess(path.stat().st_size, 2 * filled)


if __name__ == "__main__":
    unittest.main()
