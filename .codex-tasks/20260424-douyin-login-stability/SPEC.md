# Douyin Login Stability

## Goal

Improve Douyin account login stability by:

- persisting manual-verification state on accounts
- pausing aggressive relogin while verification is pending
- relaxing login validity checks beyond a narrow cookie-name allowlist

## Scope

- Django model/schema/migration changes for account verification state
- worker/login runtime updates
- frontend account payload compatibility where needed

## Out of Scope

- SMS code input workflow
- full multi-step verification UI redesign
