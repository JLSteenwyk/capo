# Grok Build in a Linux VM

A live test of Grok Build 1.0.30 succeeded in a Debian 13 ARM64 VM on macOS using Lima 2.2.0 and Apple's virtualization framework. The native `read-only` sandbox remained enabled. Installing `bubblewrap` in the guest was necessary; Grok refused to start without it.

The VM avoided the host's Docker socket symlink problem. Docker Desktop itself did not finish startup during this test, so no Docker-container result is claimed.

## Reproduce the environment

Install [Lima](https://lima-vm.io/docs/installation/), then from the repository root:

```sh
limactl start --tty=false --name=capo-grok integrations/grok/lima.yaml
limactl shell --workdir=/tmp capo-grok
```

The template has no host directory mounts, SSH-agent forwarding, or application port forwarding. Lima's SSH management connection remains available. The guest has outbound network access for installation and model requests; this is not full network isolation.

Inside the guest, install Grok using its [official instructions](https://docs.x.ai/build/overview), then use `grok login --device-code` to authenticate. Keep authentication entirely outside the public repository. Do not mount the host home directory or Docker socket into the guest.

For unattended requests, preserve Capo's options:

```sh
grok --prompt-file /path/to/private/task.txt \
  --output-format json --json-schema '<task JSON schema>' \
  --tools '' --no-subagents --sandbox read-only --permission-mode dontAsk
```

Run each request with a deadline. A missing sandbox dependency or authentication failure should fail the task rather than relax protections. Stop an unused VM with `limactl stop capo-grok`.

## What was verified

- The existing CLI login worked in the test guest with no API key supplied.
- A synthetic JSON request returned the requested object with no stderr output.
- Capo's actual `Providers.call` implementation, running inside the VM, decoded and validated a Grok implementation response.
- The proposed arithmetic correction passed three checks: positive, mixed-sign, and zero arguments.
- A separate Grok review response passed the review schema and approved the correction.
- Grok 1.0.30 uses `structuredOutput` in its JSON envelope. The adapter now supports that field and its `text` fallback, as well as the earlier envelope forms.

The copied guest login was removed after testing. Credentials, prompts, and run artifacts were not committed.

## Configure Capo's integrated transport

Copy `integrations/grok/providers.example.json` to a private configuration directory. Set its VM name and guest executable path, then select it explicitly:

```sh
export CAPO_PROVIDERS_CONFIG="$HOME/.config/capo/providers.json"
python3 -m capo doctor
python3 -m capo add "Your development objective" --repo /path/to/project --check 'python3 -m unittest discover -s tests'
python3 -m capo run OBJECTIVE_ID
```

Alternatively pass `--providers-config /path/to/providers.json` before the subcommand. Transport configuration is captured when an objective is queued, so a later environment change cannot silently reroute that objective. The Slack service uses the same selection for newly queued objectives. Local transport remains the default.

Capo checks that the VM is running, has no host mounts or forwarded sockets/agents, and has Grok, bubblewrap, and a login file. A login file is not proof of valid authentication: the actual request may still fail and will surface that failure. The adapter does not copy credentials or fall back to an API key. Sign in inside the guest and use the CLI's normal logout when retiring a dedicated guest login.

Each attempt stages only its prompt and schema in a private guest directory; repository context is already in the prompt. The worker has tools disabled and the native read-only sandbox enabled. A host heartbeat, connection EOF, cancellation marker, and remote deadline bound execution. Normal completion removes guest prompts and attempt logs, retaining a small terminal receipt. Provider-managed session history may remain in the private guest home.

Host artifacts retain output, process metadata, a unique remote attempt identity, and its terminal receipt. Timeout/cancellation includes a bounded reconciliation grace period. Recovery checks the guest receipt and fences delayed starts before permitting retry. An unreachable guest blocks recovery; restore connectivity or restart the guest and reconcile. Never assume an expired host deadline proves remote completion.

`doctor` performs read-only health checks without model calls. Start and stop the VM explicitly with Lima. No general desktop controller is installed.

This small test does not establish hostile-code containment, long-running reliability, remaining subscription capacity, or a complete Claude/Codex/Grok objective across machines.
