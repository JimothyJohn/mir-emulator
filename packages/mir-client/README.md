# mir-client

Typed Python client for the **MiR robot REST API** (`mir_client.robot`) and the
**MiR Fleet Enterprise Integration API** (`mir_client.fleet`), generated from
the official specs bundled with [mir-emulator](https://github.com/JimothyJohn/mir-emulator).

`mir_client/robot`, `mir_client/fleet`, and `_provenance.py` are entirely
generated (`scripts/generate_client.py`); CI fails if they drift from the spec
registry. Never hand-edit them.

```python
from mir_client import robot_client, fleet_client
from mir_client.robot.api.default import get_status

client = robot_client("http://192.168.12.20")   # or the emulator
status = get_status.sync(client=client)
print(status.state_text, status.battery_percentage)
```
