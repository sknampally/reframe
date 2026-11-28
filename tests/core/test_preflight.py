import tempfile
import unittest

from reframe.core import preflight
from reframe.core.errors import Outcome, Stop
from reframe.core.shell import Aws

from tests.fakes import FakeRunner


SOURCE = tempfile.mkdtemp()


def runner(*, sts=(0, {"Account": "000000000000"}, ""), terraform="Terraform v1.17.2\n", mcp=0, git=(0, "a" * 40 + "\n", "")):
    fake = FakeRunner()
    fake.add(["git", "-C", SOURCE, "rev-parse", "--show-toplevel"], 0, SOURCE + "\n")
    fake.add(["aws", "sts"], sts[0], sts[1], sts[2])
    fake.add(["terraform", "version"], 0, terraform)
    fake.add(["uvx"], mcp, "", "" if mcp == 0 else "not cached")
    fake.add(["git", "-C"], *git)
    return fake


def run(fake, ready=lambda: None, which=lambda tool: "/bin/" + tool):
    return preflight.check(aws=Aws("p", "us-east-1", fake), terraform="terraform", source=SOURCE, ref=None,
                           transform_ready=ready, runner=fake, which=which)


class PreflightTest(unittest.TestCase):
    def test_all_present(self):
        report = run(runner())
        self.assertEqual(report.problems, [])
        self.assertEqual((report.terraform, report.commit), ("1.17.2", "a" * 40))
        preflight.require(report)

    def test_expired_signin_gives_the_login_command(self):
        report = run(runner(sts=(255, "", "Error when retrieving token from sso: Token has expired and refresh failed")))
        with self.assertRaises(Stop) as stop:
            preflight.require(report)
        self.assertEqual(stop.exception.outcome, Outcome.WAITING_SIGNIN)
        self.assertIn("aws sso login --profile p", stop.exception.message)

    def test_old_terraform_and_missing_pieces_listed_together(self):
        report = run(runner(terraform="Terraform v0.11.13\n", mcp=1, git=(128, "", "fatal")), which=lambda t: None if t == "git" else "/bin/" + t)
        text = "\n".join(report.problems)
        for expected in ("git is not on PATH", "Terraform 0.11.13 is too old", "MCP server is not installed", "is not a commit"):
            self.assertIn(expected, text)
        with self.assertRaises(Stop) as stop:
            preflight.require(report)
        self.assertEqual(stop.exception.outcome, Outcome.REFUSED)

    def test_a_signin_that_names_no_account_is_refused(self):
        report = run(runner(sts=(0, {}, "")))
        self.assertTrue(any("did not say which account" in p for p in report.problems), report.problems)
        with self.assertRaises(Stop):
            preflight.require(report)

    def test_transform_not_ready(self):
        def ready():
            raise RuntimeError("No Transform profile found for this account")
        report = run(runner(), ready)
        self.assertTrue(any("AWS Transform is not ready" in p for p in report.problems))


if __name__ == "__main__":
    unittest.main()
