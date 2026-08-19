import pytest

from invariant.core.privacy import PrivacyScanner


def patterns(findings):
    return {f.pattern for f in findings}


class TestDetection:
    def setup_method(self):
        self.scanner = PrivacyScanner()

    def test_home_path_with_a_username(self):
        found = self.scanner.scan_text("see /Users/someone/projects/app for details")
        assert "home_paths" in patterns(found)

    def test_bare_home_prefix_is_not_a_match(self):
        assert self.scanner.scan_text("stored under /Users") == []

    def test_tilde_is_the_accepted_form(self):
        assert patterns(self.scanner.scan_text("stored under ~/projects/app")) == set()

    def test_email(self):
        assert "emails" in patterns(self.scanner.scan_text("write to a.b@example.com"))

    def test_ip_address(self):
        assert "ip_addresses" in patterns(self.scanner.scan_text("host 192.168.1.44"))

    def test_version_number_is_not_an_ip(self):
        assert "ip_addresses" not in patterns(self.scanner.scan_text("version 1.2.3"))

    def test_url(self):
        assert "links" in patterns(self.scanner.scan_text("see https://example.invalid/x"))

    def test_git_remote_form(self):
        assert "links" in patterns(self.scanner.scan_text("git@host.invalid:org/repo.git"))

    def test_token_shapes(self):
        samples = [
            "ghp_" + "a" * 20,
            "AKIA" + "B" * 16,
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMj.SflKxwRJSMeKKF2Q",
        ]
        for sample in samples:
            assert "tokens" in patterns(self.scanner.scan_text(sample)), sample

    def test_uuid_and_mac_are_device_ids(self):
        # Opt-in: device_ids is implemented but outside the default set — its
        # 40-hex arm matches every full git commit hash (see DEFAULT_PATTERNS).
        found = PrivacyScanner(patterns=["device_ids"]).scan_text(
            "id 123e4567-e89b-12d3-a456-426614174000 mac a1:b2:c3:d4:e5:f6"
        )
        assert "device_ids" in patterns(found)


class TestClockTimes:
    def setup_method(self):
        self.scanner = PrivacyScanner()

    def test_a_clock_time_is_flagged(self):
        assert "clock_times" in patterns(self.scanner.scan_text("the crash fired at 14:05"))

    def test_a_bare_date_is_fine(self):
        assert self.scanner.scan_text("shipped on 2026-07-28") == []

    def test_an_iso_instant_is_flagged(self):
        assert "clock_times" in patterns(self.scanner.scan_text("at 2026-07-28T13:42:09Z"))

    def test_a_duration_is_not_a_time(self):
        # The false-alarm guard exists because durations and ratios are shaped
        # exactly like clock times, and a noisy gate gets switched off.
        assert self.scanner.scan_text("the build took 12:30 of wall clock") == []

    def test_a_version_line_is_not_a_time(self):
        assert self.scanner.scan_text("version 10:15 of the spec") == []


class TestRedaction:
    def test_findings_redact_by_default(self):
        found = PrivacyScanner().scan_text("mail a.person@example.com now")
        assert "a.person@example.com" not in found[0].describe()
        assert "█" in found[0].describe()

    def test_reveal_is_opt_in(self):
        found = PrivacyScanner().scan_text("mail a.person@example.com now")
        assert "a.person@example.com" in found[0].describe(reveal=True)

    def test_a_short_prefix_survives_so_a_human_can_recognise_it(self):
        found = PrivacyScanner().scan_text("mail zz@example.com now")
        assert found[0].redacted.startswith("zz")


class TestConfiguration:
    def test_pattern_subset(self):
        scanner = PrivacyScanner(patterns=["emails"])
        found = scanner.scan_text("a@b.com and /Users/someone/x")
        assert patterns(found) == {"emails"}

    def test_unknown_pattern_lists_the_available_ones(self):
        with pytest.raises(ValueError, match="available: .*emails"):
            PrivacyScanner(patterns=["telepathy"])

    def test_extra_custom_pattern(self):
        scanner = PrivacyScanner(patterns=[], extra=[r"SECRET-\d+"])
        assert patterns(scanner.scan_text("code SECRET-42")) == {"custom[0]"}

    def test_allow_list_suppresses_a_match(self):
        scanner = PrivacyScanner(patterns=["emails"], allow=["noreply@example.com"])
        assert scanner.scan_text("from noreply@example.com") == []

    def test_inline_exemption(self):
        scanner = PrivacyScanner(patterns=["emails"])
        text = "from a@b.com  invariant: allow-privacy"
        assert scanner.scan_text(text) == []

    def test_from_manifest(self, make_manifest):
        m = make_manifest({"privacy": {"patterns": ["emails"], "allow": ["ok@x.com"]}})
        scanner = PrivacyScanner.from_manifest(m)
        assert scanner.names == ["emails"]
        assert scanner.scan_text("ok@x.com") == []


class TestDefaultPatternSet:
    """AUD-15: a bare ``PrivacyScanner()`` enabled all seven patterns while
    the manifest path enabled six — two construction paths that disagreed.
    One authoritative default set, spelled once."""

    def test_bare_construction_matches_the_manifest_default(self):
        from invariant.core.manifest import DEFAULTS
        from invariant.core.privacy import DEFAULT_PATTERNS

        assert PrivacyScanner().names == list(DEFAULT_PATTERNS)
        assert list(DEFAULTS["privacy"]["patterns"]) == list(DEFAULT_PATTERNS)

    def test_device_ids_is_implemented_and_opt_in(self):
        from invariant.core.privacy import DEFAULT_PATTERNS, PATTERNS

        assert "device_ids" in PATTERNS
        assert "device_ids" not in DEFAULT_PATTERNS
        found = PrivacyScanner(patterns=["device_ids"]).scan_text(
            "booted 123e4567-e89b-12d3-a456-426614174000"
        )
        assert patterns(found) == {"device_ids"}


class TestFileScanning:
    def test_scan_file_records_position(self, tmp_path):
        path = tmp_path / "doc.md"
        path.write_text("clean line\nmail a@b.com\n", encoding="utf-8")
        found = PrivacyScanner(patterns=["emails"]).scan_file(path)
        assert found[0].line == 2 and found[0].path == path

    def test_binary_file_is_skipped_not_crashed_on(self, tmp_path):
        path = tmp_path / "blob.bin"
        path.write_bytes(b"\xff\xfe\x00\x01")
        assert PrivacyScanner().scan_file(path) == []

    def test_missing_file_is_skipped(self, tmp_path):
        assert PrivacyScanner().scan_file(tmp_path / "absent.md") == []

    def test_scan_paths_walks_a_directory(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "one.md").write_text("x@y.com", encoding="utf-8")
        (tmp_path / "a" / "two.md").write_text("clean", encoding="utf-8")
        found = PrivacyScanner(patterns=["emails"]).scan_paths([tmp_path])
        assert len(found) == 1


class TestNonIdentifyingAddresses:
    """A no-reply address is not a way to reach anyone.

    The git co-author trailer carrying one is a convention every
    agent-assisted repository uses, so flagging it blocks the commit that
    records authorship — on every commit — until someone disables the whole
    email pattern.
    """

    def setup_method(self):
        self.scanner = PrivacyScanner(patterns=["emails"])

    def test_the_agent_coauthor_trailer_passes(self):
        assert self.scanner.scan_text("Co-Authored-By: An Agent <noreply@example.com>") == []

    def test_hyphenated_and_donotreply_forms(self):
        for address in ("no-reply@example.com", "donotreply@example.com",
                        "do-not-reply@example.com"):
            assert self.scanner.scan_text(address) == [], address

    def test_a_noreply_subdomain_passes(self):
        assert self.scanner.scan_text("a-user@users.noreply.example.com") == []

    def test_a_real_address_in_the_same_trailer_is_still_a_finding(self):
        # Narrow on purpose: only shapes that cannot identify anyone.
        found = self.scanner.scan_text("Co-Authored-By: A Person <a.person@example.com>")
        assert len(found) == 1

    def test_a_lookalike_local_part_is_still_a_finding(self):
        assert self.scanner.scan_text("noreplyactually@example.com") != []


class TestLinksNameANetworkHost:
    """BUG_044: a link is a URI whose AUTHORITY names a network host.

    The pattern used to match any scheme followed by ``://``, which made every
    private-scheme identifier a finding. Measured on two architecture documents
    in a consuming project: 26 findings, 20 of them false, in the eleven forms
    of ``PRIVATE_SCHEME_IDENTIFIERS`` below — several with no authority at all.

    Scheme breadth is the wrong axis; the authority is the right one. So the
    corpora below are built by AUTHORITY CLASS and crossed with a range of
    schemes, because the law is "the authority decides and the scheme does
    not" — a list of the literal strings that happened to be observed would
    pin the instances instead.

    Fixture note: the network-host corpus uses reserved names
    (``.example``/``.invalid``, RFC 2606) rather than the real hosts of the
    downstream evidence, which is this file's established convention — a
    privacy suite whose fixtures are real addresses is a privacy leak. The
    substitution is sound precisely because the law is structural: a reserved
    name and a real one are the same class member.
    """

    #: Verbatim from the downstream evidence. Every one is a documented
    #: identifier FORMAT written with placeholders; none addresses a network
    #: resource, and none can leak anything.
    PRIVATE_SCHEME_IDENTIFIERS = (
        "applephotos://asset/<lid>",
        "applephotos://livePhoto/<localIdentifier>",
        "appleshare://<uuid>",
        "appleinspect://capture/<hash>/<ts>",
        "encrypted-media://",
        "encrypted-media://local/segment?file=<abs>",
        "scan://<uuid>",
        "scan://<sessionUUID>",
        "scan://<uuid>/document",
        "file://",
        "file://blob",
    )

    #: Authorities that name no network host: absent, or a bare label with no
    #: dot — an opaque payload kind, or a placeholder for one.
    NON_HOST_AUTHORITIES = (
        "",            # no authority at all
        "asset",       # a bare payload-kind label
        "livePhoto",
        "local",
        "capture",
        "blob",
        "<uuid>",      # a placeholder, as documents write them
        "<sessionUUID>",
        "v1.2",        # dotted, but the suffix is not TLD-like
        "a.b",         # dotted, but the suffix is one character
    )

    #: Authorities that DO name a network host: a dotted label with a
    #: TLD-like suffix, or an IP literal.
    HOST_AUTHORITIES = (
        "a.example",
        "sub.domain.example",
        "www.a.example",
        "a.invalid",
        "host.example.invalid",
        "user@a.example",   # userinfo does not stop it being a host
        "192.0.2.10",       # IPv4 literal
        "192.0.2.10:8443",  # ...with a port
        "[2001:db8::1]",    # IPv6 literal
    )

    #: The axis that is NOT the test. Every one of these is crossed with both
    #: authority corpora, so a verdict that tracked the scheme would show up as
    #: a whole row of failures.
    SCHEMES = ("http", "https", "ftp", "file", "scan", "applephotos",
               "encrypted-media", "myscheme", "x-private+v2")

    TRAILERS = ("", "/", "/path", "/path/to/thing?q=<placeholder>#frag")

    def setup_method(self):
        self.scanner = PrivacyScanner()

    def flagged(self, text):
        return "links" in patterns(self.scanner.scan_text(text))

    # -- the corpora are non-empty ------------------------------------
    #
    # "examined nothing" and "found nothing" are the same green otherwise.

    def test_every_corpus_is_non_empty(self):
        assert len(self.PRIVATE_SCHEME_IDENTIFIERS) == 11
        assert len(self.NON_HOST_AUTHORITIES) >= 10
        assert len(self.HOST_AUTHORITIES) >= 9
        assert len(self.SCHEMES) >= 9
        assert len(self.TRAILERS) >= 4

    # -- direction 1: a non-host authority is not a link ---------------

    def test_the_downstream_false_positives_are_not_links(self):
        leaked = [s for s in self.PRIVATE_SCHEME_IDENTIFIERS if self.flagged(s)]
        assert leaked == []

    def test_no_scheme_makes_a_non_host_authority_a_link(self):
        checked = 0
        leaked = []
        for scheme in self.SCHEMES:
            for authority in self.NON_HOST_AUTHORITIES:
                for trailer in self.TRAILERS:
                    checked += 1
                    text = f"{scheme}://{authority}{trailer}"
                    if self.flagged(text):
                        leaked.append(text)
        assert checked == len(self.SCHEMES) * len(self.NON_HOST_AUTHORITIES) * len(self.TRAILERS)
        assert leaked == []

    def test_a_private_scheme_identifier_in_running_prose_is_not_a_link(self):
        assert not self.flagged("the asset is addressed as scan://<uuid>/document here")

    # -- direction 2: a network-host authority is still a link ---------

    def test_every_scheme_over_a_host_authority_is_a_link(self):
        checked = 0
        missed = []
        for scheme in self.SCHEMES:
            for authority in self.HOST_AUTHORITIES:
                for trailer in self.TRAILERS:
                    checked += 1
                    text = f"{scheme}://{authority}{trailer}"
                    if not self.flagged(text):
                        missed.append(text)
        assert checked == len(self.SCHEMES) * len(self.HOST_AUTHORITIES) * len(self.TRAILERS)
        assert missed == []

    def test_a_custom_scheme_pointing_at_a_real_host_is_still_a_link(self):
        # The regression the obvious narrowing (an allowlist of http/https/ftp)
        # would have caused, and the reason the axis is the authority.
        assert self.flagged("myscheme://internal.example.invalid/secret")

    def test_a_web_address_at_the_end_of_a_sentence_is_still_a_link(self):
        assert self.flagged("documented at https://a.example.")

    def test_two_identifiers_on_one_line_are_judged_separately(self):
        found = self.scanner.scan_text("scan://<uuid> and https://a.example/x")
        assert [f.raw for f in found if f.pattern == "links"] == ["https://a.example/x"]

    # -- the other two arms are untouched ------------------------------

    def test_the_www_arm_still_works(self):
        assert self.flagged("see www.a.example for details")

    def test_the_scp_arm_still_works(self):
        assert self.flagged("git@host.invalid:org/repo.git")
