# Founder Authority Key

This directory is part of the product enforcement control plane.

Product provisioning must install:

- `public-key.pem`: the Founder public key corresponding to a private key held
  outside the repository;
- `public-key.sha256`: SHA-256 of the exact public-key file bytes.

The private key must never be committed, placed in a prompt, or exposed to a
Product Chief or GitHub Actions job. Founder approval is a signed attestation
bound to Product ID, repository, action, stage, artifact digest and PR HEAD.
GitHub Code Review remains a separate required control.

Key rotation is a control-plane change. It requires a separate Founder
attestation and a protected review path. A PR may not replace the trusted key
and use the replacement key to approve itself.
