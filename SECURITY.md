# Security policy

DiskSprawl is a self-hosted application that reads the folders of your servers and NAS: their
names, sizes and structure, and the capacity of their volumes. A vulnerability could expose that
information, let someone start scans that load your storage, or reach more of the machine than
DiskSprawl should, so reports are taken seriously.

## Supported versions

Only the latest release is supported: the `drdonoso/disksprawl:latest` image, which is also the
newest CalVer tag (for example `20261115`). Fixes ship as a new release; there are no backports
to older tags.

## Reporting a vulnerability

**Please do not open a public issue or pull request for security problems.**

Report them privately through GitHub's private vulnerability reporting:

1. Open the [vulnerability report form](https://github.com/DrDonoso/disksprawl/security/advisories/new),
   also reachable from the repository's **Security** tab with **Report a vulnerability**.
2. Describe the problem and submit it. The report stays private between you and the maintainer.

If you cannot use the form, contact [@DrDonoso](https://github.com/DrDonoso) through the contact
details on their GitHub profile, and share the details only once you have a private channel.

Please include:

- the affected version or image tag (the running version is returned by `/api/version`);
- steps to reproduce, ideally a minimal proof of concept;
- the impact: what an attacker could read, change or disrupt, and the access they need (anyone on
  the network, a signed-in user, or a malicious web page visited by a user);
- any suggested fix or mitigation.

**Never include real folder names, host names, addresses, passwords, session cookies or database
backups.** Reproduce the problem with synthetic data on your own instance, for example the local
Docker Compose stack with its sample tree described in
[CONTRIBUTING.md](CONTRIBUTING.md#running-locally).

Expect an initial response within 7 days. DiskSprawl follows coordinated disclosure: the
maintainer confirms the problem, keeps you informed, releases a fix and then publishes a security
advisory that credits you, unless you prefer otherwise. Please keep the details private until the
fix is released, and allow a reasonable time for it.

## Scope

**In scope:**

- The optional password and session handling: sign-in, cookies, throttling and sign-out.
- Cross-site request forgery, origin and host checks, and DNS rebinding.
- Reaching files or folders outside `/data`, writing to the analyzed folders, or reading file
  contents.
- Injection through folder names, exclusion patterns, cron expressions or other input: into the
  engines' command lines, SQL, HTML or headers.
- Exposure of folder names or other data through the API, the interface, caches or logs.
- The container image, the Compose files, the engine installer and the CI workflows in this
  repository.

**Out of scope:**

- Deployments that ignore the documented setup: folders mounted read-write, the container run
  privileged or as root, its hardening removed, or an open installation (without `AUTH_PASSWORD`)
  reachable by untrusted users.
- Load caused on purpose by someone allowed to use DiskSprawl, such as scanning with many threads,
  and denial of service that requires very large resources.
- Social engineering, phishing and physical access.
- Vulnerabilities in ncdu, gdu, GNU coreutils or other dependencies without an exploitable path in
  DiskSprawl: report them upstream.
- The [public demo](https://demo.disksprawl.drdonoso.com), which runs entirely in the browser with
  synthetic data.

## Security model and deployment hardening

The [security model](docs/security-model.md) describes how DiskSprawl protects access and data
(read-only design, the optional password, origin and host checks, headers) and the checklist every
deployment should follow.
