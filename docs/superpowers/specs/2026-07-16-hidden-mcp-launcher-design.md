# Hidden MCP Launcher Design

## Goal

Start every scheduled MCP server without displaying a console window while preserving the existing
security context and user-profile behavior.

## Scope

- Keep the `AtLogOn` trigger, current-user principal, `Interactive` logon type, and each MCP's existing
  `Highest` or `Limited` run level.
- Update both the suite installer and every per-MCP `install_task.ps1` path.
- Use one shared PowerShell launcher for scheduled and immediate background starts.
- Do not convert MCP servers to `AtStartup`, `SYSTEM`, S4U, password logon, or Windows services.

## Architecture

Add a shared launcher under `scripts/`. The Scheduled Task action invokes `powershell.exe` with
`-NoProfile`, `-NonInteractive`, `-ExecutionPolicy Bypass`, and `-WindowStyle Hidden`, passing the resolved
Python executable, server script, working directory, MCP name, and log directory as arguments.

The launcher validates all required paths, sets `PYTHONIOENCODING=utf-8`, changes to the MCP working
directory, redirects the Python process's standard output and standard error to separate files, and
returns the Python process exit code. It does not detach Python, so Task Scheduler continues to track the
long-running launcher action.

## Logging

Logs are stored below a repository-local ignored log directory, with one stdout and one stderr file per
MCP. Before a server starts, any log larger than 10 MiB is rotated to the same name with a `.1` suffix.
Only one previous generation is retained. Rotation failure is fatal and reported through the launcher's
exit code because silently allowing unbounded logs would violate the size policy.

Concurrent launches of the same MCP are prevented by the Scheduled Task's existing single-instance
behavior. The launcher nevertheless opens logs in append mode so restarts preserve recent diagnostics.

## Error Handling

- Missing Python, server, or working-directory paths cause a non-zero exit before server launch.
- Failure to create the log directory or rotate/open a log causes a non-zero exit.
- The Python server's exit code becomes the launcher's exit code.
- Installer registration errors retain the existing fail-fast behavior.

## Tests

PowerShell tests will exercise the launcher with a temporary fake server process and verify:

- stdout and stderr are captured separately with no launcher console dependency;
- existing logs are appended below the size limit;
- logs over 10 MiB are rotated once;
- invalid paths return a non-zero exit;
- generated Scheduled Task actions call the shared hidden launcher and retain the current trigger,
  principal, and run level.

The existing repository validation and MCP test scripts will run after the focused tests.

## Documentation

Update installation and MCP documentation to state that tasks start at user logon through the hidden
launcher, identify the log location and rotation policy, and provide commands for inspecting launch
failures.
