import unittest

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


if __name__ == "__main__":
    unittest.main()
