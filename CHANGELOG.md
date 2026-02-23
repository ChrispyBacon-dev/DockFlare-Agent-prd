# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Cloudflare Access Service Token support for Agent transport. When the DockFlare Master is protected by Cloudflare Access policies, set `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` to authenticate without IP bypass. Enables deployment on any host regardless of network origin.
- New `transport.py` module centralizing auth headers (Bearer API key and optional CF-Access headers).
