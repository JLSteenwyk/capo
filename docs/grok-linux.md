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

## Remaining integration

This validates a Linux execution route, not automatic VM routing in Capo. The existing adapter still invokes local `grok`. A production VM transport needs explicit configuration, task staging, bounded remote-process cancellation, result retrieval, and authentication lifecycle handling. Do not replace the local executable with an unbounded SSH wrapper.

This small test does not establish hostile-code containment, long-running reliability, remaining subscription capacity, or a complete Claude/Codex/Grok objective across machines.
