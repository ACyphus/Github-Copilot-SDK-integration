# Security Policy

## Supported Versions

The latest release of this integration is actively supported with security updates.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | ✅ Yes             |
| Older   | ❌ No              |

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public GitHub issue.

Instead, use GitHub's private vulnerability reporting feature:

1. Go to the [Security tab](https://github.com/ACyphus/Github-Copilot-SDK-integration/security) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the details including steps to reproduce, expected behavior, and impact assessment.

You can expect an acknowledgement within a few days. We will work with you to understand and address the issue before any public disclosure.

## Security Best Practices

- **Never commit API tokens or credentials** to the repository.
- **Keep your GitHub Personal Access Token secure** — treat it like a password.
- **Rotate tokens regularly** and revoke any that may have been exposed.
- The bridge stores `COPILOT_GITHUB_TOKEN`; Home Assistant Core stores only the
  separate bridge connection token in remote mode.
