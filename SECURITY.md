# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Older releases | No |

## Reporting a Vulnerability

If you discover a security vulnerability in Footprinter, please report it responsibly.

**Preferred:** Use [GitHub's private security advisory](https://github.com/swellcitygroup/footprinter/security/advisories/new) to report the issue. This creates a private channel between you and the maintainers.

**Alternative:** Email [hello@swellcity.ai](mailto:hello@swellcity.ai) with a description of the vulnerability.

**Please do not** open a public GitHub issue for security vulnerabilities.

## What to Expect

- **Acknowledgment** within 72 hours of your report
- **Status update** within 7 days with an assessment and expected timeline
- **Resolution target** of 90 days for confirmed vulnerabilities
- **Credit** in the security advisory unless you prefer to remain anonymous

## What Qualifies

Security issues in Footprinter include, but are not limited to:

- **Credential handling** — OAuth token exposure, API key leaks
- **File access** — path traversal, unauthorized file reads
- **MCP permissions** — access control bypasses in the MCP server
- **Data exposure** — unintended disclosure of indexed personal data
- **Injection** — command injection, SQL injection in the database layer

## Scope

This policy covers the `footprinter-cli` Python package and its published dependencies. It does not cover:

- Connector plugin packages (e.g. `footprinter-google`) — report those to their respective maintainers
- Third-party services (Claude Desktop, any APIs a connector talks to)
- User configuration errors
- Issues in dependencies maintained by other projects (report those upstream)
