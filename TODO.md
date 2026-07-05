# Developer-experience roadmap

What integrators building against MiR robots need from this project, in the
order we intend to ship it. Open items carry their acceptance bar so "done"
is testable, not vibes; shipped items are compressed to one line each at the
bottom.

North star: [Stripe's API documentation](https://docs.stripe.com/api) — the
reference bar for developer experience. Applied here: runnable samples per
operation with a persistent language switcher, versioned surfaces where the
picker switches *everything*, and interfaces generated from the source of
truth rather than hand-maintained.

## Now

- [ ] **Case-study scenarios** (in review, PR #26). Seven self-contained
      `uv run` scripts replaying published MiR customer deployments against
      the emulator — together a guided tour of sessions, missions, the queue,
      faults, registers, metrics, latency shaping, and the Fleet API.
      Acceptance: each script passes unmodified against a fresh emulator.

- [ ] **Scenario CI lane.** Once #26 lands, run the scenarios in CI as an
      end-to-end smoke of the whole surface — they exercise paths unit tests
      don't compose. Acceptance: a CI job boots the robot and fleet
      emulators, runs all scenarios, and fails the build on any non-zero
      exit.

- [ ] **Publish `mir-client` to PyPI.** The SDK is generated, gated, and
      contract-tested, but only installable from a checkout; `release.yml`
      already builds attested wheels and has a publish step waiting on
      credentials. Prefer PyPI trusted publishing (OIDC) over a long-lived
      `PYPI_API_TOKEN`, per the note in the workflow. Acceptance:
      `pip install mir-client` in a clean venv, drive a local emulator with
      `robot_client()`.

## Next

- [ ] **Richer fleet order phases.** Fallback missions, priorities that
      influence robot choice, and evacuation/zone behaviors — the open half
      of the fleet emulation work. Source of truth first: behavior must be
      pinned by the official Fleet docs, not guessed. Acceptance: an order
      whose primary mission faults falls back as documented, and a
      higher-priority order observably preempts robot selection.

- [ ] **Connection-drop injection.** The missing fault class from latency
      shaping: an ASGI app can't portably sever a socket mid-response, so
      this needs a raw-transport shim in front of uvicorn. Acceptance: a
      `connection_drop` fault makes an in-flight request die with a
      transport error (not an HTTP status), and clients observe the same
      failure shape a rebooting robot produces.

- [ ] **Path realism from site maps.** Positions currently interpolate
      along a canned patrol loop; ride the fleet site model so executing
      missions move along map-plausible waypoints. Acceptance: a mission
      between two named positions reports coordinates that traverse the
      site map's path, not a straight line.

## Later

- [ ] **ROS-bridge protocol emulation.** Faithful rosbridge
      subscribe/publish on :9090 plus fleet event streams — the fidelity
      projects deferred from the WebSocket status push, each needing its
      own primary-source work before any code.

- [ ] **Reference docs contingency.** We deliberately deleted our
      Stripe-style reference in favor of linking MiR's official docs. If
      MiR ever unpublishes the Fleet Swagger UI or the portal's REST API
      files, resurrect ours from the scraped registry.

## Shipped
