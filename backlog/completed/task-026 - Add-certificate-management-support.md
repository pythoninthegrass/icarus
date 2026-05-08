---
id: TASK-026
title: Add certificate management support
status: Done
assignee: []
created_date: '2026-03-23 18:05'
updated_date: '2026-03-25 21:25'
labels:
  - gap-analysis
  - new-resource
milestone: m-0
dependencies: []
references:
  - main.py
  - schemas/dokploy.schema.json
priority: low
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The TF provider can upload custom SSL certificates with auto-renewal. Icarus only sets `certificateType` on domains (letsencrypt/none) but can't manage custom certificates. Add certificate config for uploading custom certs and associating them with domains.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Can declare certificates in dokploy.yml (name, cert data path, key path, auto_renew)
- [x] #2 Certificates are uploaded during setup
- [x] #3 Domains can reference custom certificates by name
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## Summary

Added certificate management support to icarus, allowing users to declare custom SSL/TLS certificates in `dokploy.yml` and associate them with domains.

## Changes

### Config & Schema
- `schemas/dokploy.schema.json`: Added `certificates` top-level array with `certificate_entry` definition (name, certFile, keyFile, autoRenew). Added `custom` to domain `certificateType` enum and `certificate` field to domain definition with conditional requirement.
- `dokploy.yml.example`: Added certificate configuration example.
- `docs/configuration.md`: Added Certificates section with table and domain reference example. Fixed domain table alignment.

### Payload Builders (`src/icarus/payloads.py`)
- `build_certificate_create_payload()`: Reads PEM cert/key files (relative to repo root), builds API payload with `name`, `certificateData`, `privateKey`, `organizationId`, and optional `autoRenew`.
- `build_domain_payload()`: Now includes `customCertResolver` when domain has `certificate` field.

### Setup Flow (`src/icarus/commands.py`)
- `cmd_setup`: Extracts `organizationId` from project creation response. Added section 2.7 for certificates after destinations — checks `certificates.all` for existing, creates if missing, stores in `state["certificates"]`.
- `cmd_apply`: Calls `reconcile_certificates` during redeploy.

### Reconciliation (`src/icarus/reconcile.py`)
- `reconcile_certificates()`: Creates missing certificates, skips existing (matched by name). Follows registries/destinations pattern.
- `reconcile_domains()`: Detects `customCertResolver` changes during domain updates.

### Plan (`src/icarus/plan.py`)
- `_plan_initial_setup`: Shows certificate creates and domain `certificate` attribute.

### Tests (15 new tests in `tests/test_unit.py`)
- `TestBuildCertificateCreatePayload`: Payload builder with file reads, relative paths, auto_renew default.
- `TestBuildDomainPayloadCustomCert`: Domain payload with/without custom cert.
- `TestCmdSetupCertificates`: Setup creates certs, saves state, reuses existing.
- `TestReconcileCertificates`: Create, skip existing, skip when none.
- `TestPlanCertificates`: Initial setup plan entries.
- `TestDomainReconciliationCustomCert`: Domain create/update with custom cert.

### New Fixture
- `tests/fixtures/certificate_config.yml`: Config with certificate and domain referencing it.
<!-- SECTION:FINAL_SUMMARY:END -->
