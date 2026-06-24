# CI/CD

GitHub Actions is used for tests, code quality, and optional deployment to the
Raspberry Pi 5 through a self-hosted runner. There is no registry build, AWS deploy,
or cloud runtime in the current architecture.

---

## Workflow overview

```text
push / PR to main
       │
       ▼
e2e-tests.yml
  ├── unit-tests.yml
  ├── integration-tests.yml
  └── sonar-quality-gate
       │
       │ main branch only, after tests pass
       ▼
deploy.yml ─────────────────────────────► Raspberry Pi 5
  sync source, build locally, run scripts/pi_deploy.sh
```

Workflow files:

| File | Trigger | Purpose |
| --- | --- | --- |
| `e2e-tests.yml` | push / PR / manual | Orchestrates unit, integration, and SonarCloud checks |
| `unit-tests.yml` | called by e2e | Pure unit tests |
| `integration-tests.yml` | called by e2e | PostgreSQL-backed integration tests |
| `deploy.yml` | e2e success on main or manual | Deploys locally on the Pi runner |

---

## Pi deployment

`deploy.yml` runs on a self-hosted runner labelled:

```text
self-hosted, linux, arm64, pi5
```

The job uses the GitHub `prod` environment, so `core-infra/terraform/github`
can enforce required review before deployment. It does not receive broker keys or
portfolio credentials from GitHub. Runtime secrets stay in the Pi deployment
directory’s `.env` file.

Deployment steps:

1. Check out the tested commit.
2. Sync source into `PI_DEPLOY_DIR` or `$HOME/investments-assistant`.
3. Preserve `.env`, `models/`, generated reports, and Nginx certificates.
4. Run `bash scripts/pi_deploy.sh`.
5. Smoke-test `GET /api/health` from inside the app container.

---

## Runner setup

On the Pi:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o runner.tar.gz -L \
  https://github.com/actions/runner/releases/latest/download/actions-runner-linux-arm64-*.tar.gz
tar xzf runner.tar.gz
./config.sh \
  --url https://github.com/Investments-Assistant/investments-assistant \
  --token <REGISTRATION_TOKEN> \
  --labels pi5 \
  --name pi5-runner
sudo ./svc.sh install
sudo ./svc.sh start
```

Keep persistent runtime files in the deploy directory:

```text
$HOME/investments-assistant/.env
$HOME/investments-assistant/models/
$HOME/investments-assistant/config/nginx/certs/
```

---

## GitHub IaC

GitHub organisation, repositories, teams, branch protections, Actions environments,
and repository variables are managed in `core-infra/terraform/github`.

Useful repository variable:

| Variable | Purpose |
| --- | --- |
| `PI_DEPLOY_DIR` | Optional deploy directory override. Defaults to `$HOME/investments-assistant`. |

Repository secrets are only needed for CI services such as `SONAR_TOKEN`.
