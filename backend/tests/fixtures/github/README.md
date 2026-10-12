Fixtures for `tests/test_github_metrics.py`. No token or network is needed to use them.

- `pulls.json`, `reviews.json`, `workflow_runs.json`: **recorded** from the public
  `Sujendra-23/Autonomous-Ops-Intelligence` REST API (pulls, reviews, and `release-checks.yml`
  runs), trimmed to the fields the ingest reads. That repo is solo-maintained, so every review
  list is empty; review-latency logic is covered by hand-built payloads in the tests.
- `deployments.json`, `deployment_statuses.json`: **synthetic**, shaped like the GitHub
  Deployments API (that repo has no deployments to record).
