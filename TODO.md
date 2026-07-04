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

- [x] **Stripe-style API reference, per software version.** A reference
      section generated in-browser from the selected version's swagger:
      operations grouped by resource, expandable per-operation blocks with
      path-parameter and body-attribute tables (types, required flags,
      nested children), documented response codes, and a code sample in the
      active language. The existing version picker switches the entire
      reference — diffing 2.x vs 3.x docs is two clicks. Acceptance: every
      operation in the served swagger appears; samples honor the selected
      version; works on the public demo and against a local `mir-emulator`.

## Next

- [ ] **Version-diff surface.** `GET /_emulator/diff?from=2.14.7&to=3.8.1`
      returning added/removed/changed operations and definitions, plus a
      console view. The scraper's `diff.py` already computes this at scrape
      time; expose it at runtime so integrators can preflight a fleet
      upgrade. Acceptance: diff of the pinned 3.5.4 against 3.5.6 reports no
      structural changes (the converter-oracle invariant, now user-visible).

- [ ] **Fault injection.** A `/_emulator/faults` control surface (and/or
      scenario file) that drives the robot into the states integrators must
      handle: emergency stop, localization lost, battery critical, blocked
      path, mission failure. Documented error payloads matching the spec's
      400/404/409 shapes. Acceptance: each fault flips `/status` state_id and
      errors[] the way the real robot documents, and clears on demand.

- [ ] **Mission lifecycle realism, deepened.** Time-based mission
      progression with position interpolation along a path, configurable
      durations, battery drain tied to motion. Builds on the per-session
      statefulness that already exists. Acceptance: enqueue → poll shows
      Pending → Executing → Done with plausible intermediate positions.

- [ ] **Latency and network shaping.** Configurable response delay, jitter,
      and connection drops (`X-MiR-Latency` header or emulator flag) so
      timeout and retry paths can be tested. Robots live on factory Wi-Fi;
      the happy path is not the interesting path. Acceptance: a request with
      shaping enabled observably delays/drops; defaults stay instant.

## Later

- [ ] **WebSocket bridge.** Real robots expose a ROS-bridge WebSocket next
      to REST; even a minimal `/status` push channel lets reactive UIs be
      tested. Gate: needs a persistent-connection deploy target (the Lambda
      demo can't hold sockets; local/container mode can).

- [ ] **MiR Fleet API emulation.** Fleet is a separate API family (its PDFs
      are deliberately excluded from the robot scrape). Most serious
      customers code against Fleet: robot assignment, mission dispatch
      across robots. Would enter the registry as its own spec family.

- [ ] **Generated Python SDK.** The tracked swaggers are machine-readable;
      publish a typed client generated from them (Python first) so emulator
      + SDK is a complete dev kit. Gate: generation must be reproducible in
      CI from the registry, never hand-edited.

- [ ] **Scenario record/replay.** Capture a request sequence against the
      emulator and replay it as a regression test — pairs with per-session
      virtual robots. Acceptance: a recorded session replays byte-identical
      (modulo timestamps) against the same version.
