/* Stand-in noita.exe for workdir instances.
 *
 * With mods enabled, the game's "New Game" exits and relaunches itself as a relative
 * `noita.exe <args>` from its working directory. In a workdir instance that finds this shim.
 * It logs the command line and cwd to rl_bench_shim.log, then, if RL_BENCH_SHIM=relaunch,
 * starts RL_BENCH_REAL_EXE with -always_store_userdata_in_workdir prepended, so the
 * relaunched game keeps the isolated profile instead of falling back to the user's saves.
 * Build: gcc -O2 -municode -o noita.exe noita_shim.c
 */
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

static const wchar_t *args_after_exe(const wchar_t *cl)
{
    int quoted = 0;
    while (*cl && (quoted || (*cl != L' ' && *cl != L'\t'))) {
        if (*cl == L'"') quoted = !quoted;
        cl++;
    }
    while (*cl == L' ' || *cl == L'\t') cl++;
    return cl;
}

int wmain(void)
{
    const wchar_t *cl = GetCommandLineW();
    wchar_t cwd[MAX_PATH], mode[32] = L"", real[MAX_PATH] = L"";
    GetCurrentDirectoryW(MAX_PATH, cwd);
    GetEnvironmentVariableW(L"RL_BENCH_SHIM", mode, 32);
    GetEnvironmentVariableW(L"RL_BENCH_REAL_EXE", real, MAX_PATH);

    FILE *f = _wfopen(L"rl_bench_shim.log", L"a");
    if (f) {
        fwprintf(f, L"tick=%lu mode=%ls cwd=%ls cmdline=%ls\n", GetTickCount(), mode, cwd, cl);
        fclose(f);
    }
    if (wcscmp(mode, L"relaunch") != 0 || !real[0]) return 0;

    static wchar_t cmd[4096];
    swprintf(cmd, 4096, L"\"%ls\" -always_store_userdata_in_workdir %ls", real, args_after_exe(cl));
    STARTUPINFOW si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(real, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) return 1;
    f = _wfopen(L"rl_bench_shim.log", L"a");
    if (f) {
        fwprintf(f, L"started pid=%lu cmd=%ls\n", pi.dwProcessId, cmd);
        fclose(f);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
