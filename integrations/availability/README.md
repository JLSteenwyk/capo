# Host availability

Run the optional availability companion alongside the Slack service:

```
python3 -m capo.availability_host --config /PRIVATE/availability.json --home /PRIVATE/capo-home
```

Keep the configuration outside Git, readable only by its owner (mode 0600):

```json
{
  "prevent_ac_sleep": true,
  "heartbeat_url": "",
  "interval": 60,
  "max_health_age": 30
}
```

On macOS this holds `caffeinate -s -w PID`: the assertion only applies on AC
power and does not hold the display awake. It makes no permanent `pmset` changes.
Battery sleep behavior remains under the OS policy. Stopping the companion
releases the assertion; setting `prevent_ac_sleep` to false and restarting also
reverses the policy. Lid closure, explicit sleep, shutdown, power loss and network
outages can still make Capo unavailable. Use a dedicated always-on host for
continuous availability. A logged-in LaunchAgent also stops being available when
its user logs out.

To persist it on macOS, create an owner-only LaunchAgent plist in
`~/Library/LaunchAgents/org.capo.availability.plist` with `Label` set to
`org.capo.availability`, `ProgramArguments` containing the absolute Python path,
`-m`, `capo.availability_host`, `--config`, the private configuration path,
`--home`, and the private runtime directory. Set `WorkingDirectory` to the Capo
checkout or verified installed release, `KeepAlive` and `RunAtLoad` to true, and
`ThrottleInterval` to 10. Set output/error paths to private runtime logs. Bootstrap
with `launchctl bootstrap gui/$(id -u) PATH_TO_PLIST`. Disable with
`launchctl bootout gui/$(id -u)/org.capo.availability`, then remove the plist if
retiring the service. Repoint the working directory when retiring a release.

## External dead-man monitoring

An external service must notice missing pings; this companion cannot detect its
own host sleeping while asleep. The exact remaining external requirement is an
owner-controlled HTTPS dead-man check endpoint, hosted independently of Capo,
with an alert destination configured by the owner. A Healthchecks-compatible
check is one option; another authorized monitoring service may be used if its
GET endpoint accepts a heartbeat and returns HTTP 2xx without redirects.

Configure the remote check for one-minute pings plus a grace period (for example,
five minutes). Store its secret ping URL only in `heartbeat_url` in the private
file. Do not place the URL in a public plist example or commit it. Redirects and
private network destinations are not supported. No messages or tokens are sent:
the request only calls the configured endpoint.

The companion pings only when the Slack health receipt is recent, connected, and
has completed a processing cycle (or is deliberately drained/in probation).
Freshness is checked using wall time so wake-up cannot turn an old receipt into
a healthy ping. Stale/missing/disconnected health withholds pings, allowing the
remote service to alert. Failed HTTP requests are recorded without exception
payloads. Private `CAPO_HOME/availability.json` distinguishes unconfigured,
withheld, failed and accepted heartbeats. An accepted ping proves receipt by the
endpoint, not that its alert route works.

Verify the setup using a controlled temporary suspension of pings on the remote
check, confirm its alert and recovery, and then restore pings. Do not deliberately
sleep a busy production host or replay old actions for a test. External monitoring
must remain described as unconfigured until a real endpoint and alert delivery
have been verified. The companion does not edit requests, send Slack messages,
or execute/retry external actions.
