# mir-mcp

MCP (Model Context Protocol) server that lets any MCP client — Claude Code,
Claude Desktop, or anything else speaking MCP — control MiR robots and
fleets in natural language. It wraps the documented MiR REST APIs (robot
`/api/v2.0.0`, fleet `/api/v1`), so it works identically against this
repo's emulator and against real hardware.

## Tools

Robot: `mir_robot_status`, `mir_set_robot_state` (ready/pause/manual),
`mir_clear_error`, `mir_list_missions`, `mir_queue_mission` (by name or
guid, optional wait-for-completion), `mir_mission_queue`,
`mir_cancel_missions`, `mir_read_register`, `mir_write_register`,
`mir_manage_faults` (emulator-only fault injection).

Fleet: `mir_fleet_robots`, `mir_fleet_dispatch` (serial orders by mission
name), `mir_fleet_order_status` (check/abort).

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `MIR_ROBOT_URL` | `http://127.0.0.1:8080` | Robot base URL |
| `MIR_FLEET_URL` | = `MIR_ROBOT_URL` | Fleet base URL |
| `MIR_USERNAME` / `MIR_PASSWORD` | `distributor` | Robot account (password pre-hash) |
| `MIR_API_KEY` | `distributor` | Fleet `x-api-key` |
| `MIR_SESSION` | unset | Emulator-only `X-MiR-Session` isolation id |

## Use with Claude Code

```sh
# terminal 1: something to control
uv run mir-emulator --fleet-version 1.5.0

# register the server (stdio)
claude mcp add mir -- uv run --project /path/to/mir-emulator mir-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mir": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mir-emulator", "mir-mcp"],
      "env": {"MIR_ROBOT_URL": "http://127.0.0.1:8080"}
    }
  }
}
```

Then talk to it: *"pause the robot"*, *"queue the charging mission and wait
for it"*, *"inject an emergency stop and show me what the fleet sees"*.

## Against real hardware

Point `MIR_ROBOT_URL` at the robot's IP and set the account env vars.
State-changing tools move a real vehicle; the fault-injection tool answers
404 on hardware (it exists only on the emulator). Keep destructive-tool
confirmation on in your client.
