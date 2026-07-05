# Developer-experience roadmap

What integrators building against MiR robots need from this project, in the
order we intend to ship it. Open items carry their acceptance bar so "done"
is testable, not vibes; completed work lives in git history, not here.

North star: [Stripe's API documentation](https://docs.stripe.com/api) — the
reference bar for developer experience. Applied here: runnable samples per
operation with a persistent language switcher, versioned surfaces where the
picker switches *everything*, and interfaces generated from the source of
truth rather than hand-maintained.

## Now

- [ ] **Publish `mir-client` to PyPI.** The SDK is generated, gated, and
      contract-tested; `release.yml` builds attested wheels and has a
      publish step waiting on credentials. BLOCKED on a PyPI-side action
      only a maintainer can take: register the project and configure
      trusted publishing (OIDC) — preferred over a long-lived
      `PYPI_API_TOKEN`, per the note in the workflow. Acceptance:
      `pip install mir-client` in a clean venv, drive a local emulator with
      `robot_client()`.

## Next

- [ ] **Cart / hook modeling.** `hook_status` currently always reports
      `available: false, cart_attached: false`, so "pick up the cart"
      succeeds by mission accounting with no physical confirmation channel
      (surfaced while driving the emulator as an operator). Source of truth
      first: pin the hook state machine to the official MiR250 Hook docs.
      Acceptance: a pickup mission at a cart position flips
      `cart_attached` with a spec-shaped cart document, and a drop-off
      clears it.

- [ ] **Richer reporting.** `mir-report` ships current status, one daily
      trend, and the action timeline. Next: per-mission-kind breakdowns,
      battery history (sampled via the status WebSocket), and multi-day
      trends once `/statistics/distance` has history to show. Acceptance:
      a report over a multi-day emulator run charts each day separately
      and groups timeline entries by mission kind.

## Later

- [ ] **ROS-bridge protocol emulation.** Faithful rosbridge
      subscribe/publish on :9090 plus fleet event streams — the fidelity
      projects deferred from the WebSocket status push, each needing its
      own primary-source work before any code.

- [ ] **Reference docs contingency.** We deliberately deleted our
      Stripe-style reference in favor of linking MiR's official docs. If
      MiR ever unpublishes the Fleet Swagger UI or the portal's REST API
      files, resurrect ours from the scraped registry.
