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
