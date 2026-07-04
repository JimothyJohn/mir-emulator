# Developer-experience roadmap

What integrators building against MiR robots need from this project, in the
order we intend to ship it. Checked items are done and verified; each item
carries its acceptance bar so "done" is testable, not vibes.

North star: [Stripe's API documentation](https://docs.stripe.com/api) — the
reference bar for developer docs. The features worth stealing, mapped to this
project: a persistent language switcher with runnable samples per operation,
a three-pane reference (nav / prose / code) generated from the source of
truth, versioned docs where the picker switches the *whole* surface, copy
buttons everywhere, and expandable attribute tables for request/response
schemas. Everything below that references "Stripe-style" means one of these.

## Now

- [x] **Track the newest 4 minor lines of every major release.**
      3.8.1 / 3.7.2 / 3.6.7 / 3.5.6 (+ pinned 3.5.4 oracle) and
      2.14.7 / 2.13.5.4 / 2.12.0.4 / 2.10.5.8, scraped from the official
      portal PDFs. Selection rule lives in `mir_spec_scraper.versions`;
      the weekly scrape keeps it current.

- [x] **Multi-language code samples in the console** (cURL, Python,
      JavaScript, Go, Rust). Stripe-style language tabs on the request
      mirror: pick a language once, every sample on the page follows, choice
      persists across visits. Samples are complete programs — they derive
      MiR's `Basic BASE64(user:SHA-256(password))` token in-language, carry
      the `X-MiR-Session` header when a virtual robot session is active, and
      target the selected software version's path prefix. Acceptance: copy
      any sample, run it unmodified, get the same response the console shows.

- [x] ~~Stripe-style API reference, per software version.~~ **Shipped,
      then deliberately removed** (2026-07-04): rebuilding reference docs
      duplicates official source material. The console now links to MiR's
      official docs instead — the public Fleet Swagger UI and the portal's
      REST API files page — and keeps only interface/sim features (console,
      catalog, samples). If MiR ever unpublishes the docs, revisit.

## Next

- [x] **Version-diff surface.** Shipped 2026-07-04:
      `GET /_emulator/diff?from=2.14.7&to=3.8.1` on the dispatcher (works
      for fleet pairs too; cross-family refused), plus a "Compare versions"
      console view. Signature granularity deliberately matches the scrape
      oracle (type/format/enum/properties; PDF-lossy keys ignored), so the
      acceptance invariant holds: 3.5.4 → 3.5.6 reports structurally
      identical, verified in tests and in the browser.

- [x] **Fault injection.** Shipped 2026-07-04: `GET/PUT/DELETE
      /_emulator/faults` per robot (session-isolated) with emergency_stop,
      error, localization_lost, battery_critical, blocked_path. Holding
      faults freeze the mission simulation and release it exactly where it
      stopped; resettable faults clear via the documented `PUT /status
      {"clear_error": true}`; the fleet reports faulted robots with official
      `robot-end-state` enum values ("Emergency Stop", "Error"). State ids
      beyond the writable {3,4,11} are pinned by MiR's own ROS message table
      (mir_msgs/RobotState.msg) — not guessed. mission_failure landed as a
      follow-up: running and queued missions abort at the sim instant
      (finished ones keep their history), the robot errors until
      clear_error, and the fleet reports the orders as Aborted.

- [x] **Mission lifecycle realism.** Already satisfied by the mission
      simulation shipped with per-session statefulness, verified against the
      acceptance bar by existing tests: Pending → Executing → Done with
      spec-shaped timestamps (`test_queue_entry_timestamps_follow_the_lifecycle`),
      position interpolation along the patrol loop while executing
      (`test_battery_drains_and_position_moves_while_executing`),
      `--mission-duration` for configurable durations, battery drain tied to
      executed seconds. Deeper path realism (waypoints from site maps) can
      ride on the fleet site model later if needed.

- [x] **Latency shaping.** Shipped 2026-07-04: `X-MiR-Latency: <ms>`
      per-request header (robot and fleet; applied after auth so 401s stay
      fast; capped at 10 s) plus a `--latency-ms` baseline flag. Acceptance
      met: shaped requests observably delay, defaults stay instant.
      Deferred: true connection *drops* — an ASGI app cannot portably sever
      a socket mid-response; needs a raw-transport shim if we want it.

## Later

*(MiR Fleet emulation graduated from this list — see above.)*

- [x] **WebSocket status push.** Shipped 2026-07-04 for local/container
      mode: `/_emulator/ws/status` streams the /status document on an
      interval (token or header auth, session-isolated, emulator-namespaced
      so no MiR surface is invented). Optional extra `mir-emulator[ws]`
      pulls the uvicorn WS protocol; the Lambda demo still can't hold
      sockets, as gated. Deferred: faithful ROS-bridge protocol (rosbridge
      subscribe/publish on :9090) and fleet event streams — bigger fidelity
      projects with their own primary-source work.

- [x] **MiR Fleet API emulation.** Shipped 2026-07-04: fleet family in the
      registry (official OpenAPI 3, 1.5.0/1.4.2/1.3.1, public URLs — no PDF
      conversion), `mir_emulator.fleet` with x-api-key auth and embedded
      robot emulators driven over their own REST API, serial-order dispatch
      into real robot mission queues, session isolation composing across
      both layers, `/fleet/<version>/` dispatcher mounts, `--fleet-version`
      CLI, credential-free scraper half. Remaining fleet follow-ups: the
      Top Module and Compatibility APIs (published beside the Integration
      API), richer order phases (fallback missions, priorities affecting
      robot choice), and evacuation/zone behaviors.

- [ ] **Generated Python SDK.** The tracked swaggers are machine-readable;
      publish a typed client generated from them (Python first) so emulator
      + SDK is a complete dev kit. Gate: generation must be reproducible in
      CI from the registry, never hand-edited.

- [x] **Scenario record/replay.** Shipped 2026-07-04: `GET/PUT/DELETE
      /_emulator/recorder` per virtual robot (robot and fleet), and
      `mir-emulator --replay scenario.json` replays against a fresh emulator.
      Acceptance exceeded: replay re-freezes the simulation clock to each
      recorded instant, so sessions reproduce **fully byte-identical** —
      timestamps included — and any divergence is a reported regression.
