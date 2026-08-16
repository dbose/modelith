# Security policy

## Reporting a vulnerability

If you find a security issue in Modelith, please report it privately rather than opening
a public issue.

Use GitHub's private vulnerability reporting on this repository
(the "Report a vulnerability" button under the Security tab), or email
bose.debasish@gmail.com with the details and, if possible, a minimal reproduction.

Please give a reasonable amount of time for a fix before any public disclosure. You will
get an acknowledgement of your report, and an update once the issue is understood and a
fix is planned.

## Scope

Modelith is a local, git-native tool: it reads and writes model files in your own
repository and, when you run `mdl serve`, binds a read-oriented server to localhost. It
has no hosted service, no authentication layer, and no telemetry. Reports that are most
useful concern the CLI, the generated dbt output, the local server, or the handling of
untrusted model or manifest input.

## Supported versions

Fixes are made against the latest released version on PyPI (`modelith-dbt`). Please
include the version you are running (`mdl --version`) in your report.
