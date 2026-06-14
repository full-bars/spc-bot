> [!IMPORTANT]
> **Project Governance Update:** This release establishes the project's first formal `PRIVACY_POLICY.md` and `TERMS_OF_SERVICE.md` to ensure safe, transparent usage and provide necessary liability protections.

This point release also stabilizes automated sounding summaries by resolving LCL extraction issues and eliminating a race condition in the posting pipeline. 

**🐛 Bug Fixes**
* **Sounding Summaries**: Resolved a race condition that could cause automated sounding summaries to fail to post in active weather scenarios.
* **LCL Extraction**: Fixed an issue with how Lifted Condensation Level (LCL) data is extracted and parsed during environmental analysis.

**📚 Documentation & Governance**
* **Privacy Policy**: Introduced a formal `PRIVACY_POLICY.md` detailing data collection practices, AI integration specifics, and user rights.
* **Terms of Service**: Added `TERMS_OF_SERVICE.md` with explicit life-safety disclaimers, acceptable use policies, and limitations of liability to better protect the open-source project.

**🔧 Maintenance**
* Bumped the Rust Python bindings (`pyo3`) dependency from v0.28.3 to v0.29.0.

--------
## What's Changed
* fix: correct LCL extraction and autopost sounding summaries without race condition by @full-bars in https://github.com/full-bars/spc-bot/pull/517
* docs: add privacy policy by @full-bars in https://github.com/full-bars/spc-bot/pull/518
* docs: add terms of service by @full-bars in https://github.com/full-bars/spc-bot/pull/519
* chore(deps): bump pyo3 from 0.28.3 to 0.29.0 by @dependabot in https://github.com/full-bars/spc-bot/pull/520

**Full Changelog**: https://github.com/full-bars/spc-bot/compare/v5.37.8...v5.37.9
