import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from reframe.core.errors import NotSent, Stop
from reframe.core.runfolder import RunFolder
from reframe.report.store import TAGS, Store


class FakeAws:
    profile, region = "p", "us-east-1"

    def __init__(self):
        self.calls, self.objects = [], {}
        self.buckets = {}  # name: tags, for finding the bucket from another computer
        self.indexed = None  # names the tag index lists; None: denied

    def write(self, *args, timeout=0):
        self.calls.append(args)
        if args[:2] == ("s3", "cp"):
            source, target = Path(args[2]), args[3]
            if source.is_file():
                self.objects[target.removeprefix("s3://").split("/", 1)[1]] = source.read_bytes()
                return {}
            for f in source.rglob("*"):
                if f.is_file():
                    self.objects[target.removeprefix("s3://").split("/", 1)[1] + str(f.relative_to(source))] = f.read_bytes()
        return {}

    def read(self, *args, timeout=0):
        self.calls.append(args)
        if args[:2] == ("s3api", "list-objects-v2"):
            return [[k, "2026-11-23T00:00:00Z"] for k in self.objects if k.endswith("manifest.json")]
        if args[:2] == ("s3api", "get-object"):
            Path(args[-1]).write_bytes(self.objects[args[args.index("--key") + 1]])
            return {}
        if args[:2] == ("resourcegroupstaggingapi", "get-resources"):
            if self.indexed is None:
                raise NotSent("aws resourcegroupstaggingapi get-resources failed: AccessDeniedException")
            return [f"arn:aws:s3:::{n}" for n in self.indexed]
        if args[:2] == ("s3api", "list-buckets"):
            return [n for n in self.buckets if n.startswith("reframe-reports-")]
        if args[:2] == ("s3api", "get-bucket-tagging"):
            return [{"Key": k, "Value": v} for k, v in self.buckets[args[3]].items()]
        if args[:2] == ("s3api", "head-bucket"):
            if args[3] not in self.buckets:
                raise NotSent("aws s3api head-bucket failed: Not Found")
            return {}
        if args[:2] == ("s3api", "list-object-versions"):
            return len(self.objects)
        if args[:2] == ("s3", "sync"):
            prefix, target = args[2].removeprefix("s3://").split("/", 1)[1], Path(args[3])
            for k, v in self.objects.items():
                if k.startswith(prefix):
                    (target / k[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)
                    (target / k[len(prefix):]).write_bytes(v)
            return {}
        raise AssertionError(args)


class FakeTerraform:
    applied, destroyed = [], []

    def __init__(self, store):
        self.store = store

    def install(self):
        pass

    def variables(self, root, values):
        self.store.folder.save(f"terraform/{root}/reframe.auto.tfvars.json", values)

    def apply(self, root, changes, backend=None):
        name = self.store.folder.load(f"terraform/{root}/reframe.auto.tfvars.json")["bucket_name"]
        FakeTerraform.applied.append(name)
        return changes.apply("terraform-reports-apply", "terraform apply", {}, lambda: {"created": ["aws_s3_bucket.reports"], "outputs": {"bucket": name}})

    def inventory(self, root, backend=None):
        return [{"root": root, "address": "aws_s3_bucket.reports", "type": "aws_s3_bucket", "name": self.store.bucket, "id": self.store.bucket}]

    def destroy(self, root, changes, backend=None):
        FakeTerraform.destroyed.append(root)
        return changes.apply("terraform-reports-destroy", "terraform destroy", {}, lambda: {"deleted": ["aws_s3_bucket.reports"]})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"REFRAME_HOME": self.tmp.name})
        self.env.start()
        self.aws = FakeAws()
        self.store = Store(self.aws, terraform="terraform")
        self.store.terraform = lambda: FakeTerraform(self.store)
        FakeTerraform.applied, FakeTerraform.destroyed = [], []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def copy(self, run, project="demo", published="2026-11-23"):
        folder = Path(self.tmp.name) / f"copy-{project}-{run}"
        (folder / "aws").mkdir(parents=True)
        (folder / "index.html").write_text(f"<h1>{project} {run}</h1>")
        (folder / "aws" / "index.html").write_text("aws")
        (folder / "manifest.json").write_text(json.dumps({"project": project, "run": run, "commit": "c" * 40, "published_at": published}))
        return folder

    def test_publish_list_open(self):
        prefix = self.store.upload(self.copy("abc123"), "demo", "abc123")
        self.assertEqual(prefix, "demo/abc123/")
        self.store.upload(self.copy("def456"), "demo", "def456")
        self.assertEqual(len(FakeTerraform.applied), 1)  # one bucket, made once
        self.assertRegex(FakeTerraform.applied[0], r"^reframe-reports-[a-z0-9]{8}$")
        cp = next(c for c in self.aws.calls if c[:2] == ("s3", "cp"))
        self.assertIn("--sse", cp)
        self.assertEqual(sorted(r["run"] for r in self.store.reports()), ["abc123", "def456"])
        page = self.store.download("demo/abc123")
        self.assertEqual(page.read_text(), "<h1>demo abc123</h1>")

    def test_teardown_needs_yes_and_a_new_publish_makes_a_new_bucket(self):
        self.store.upload(self.copy("abc123"), "demo", "abc123")
        state = self.store.folder.path("terraform/reports/terraform.tfstate")
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}")
        said = []
        self.assertFalse(self.store.teardown(lambda q: "no", said.append))
        self.assertEqual(FakeTerraform.destroyed, [])
        self.assertTrue(self.store.teardown(lambda q: "yes", said.append))
        self.assertEqual(FakeTerraform.destroyed, ["reports"])
        self.assertIsNone(self.store.bucket)
        self.assertEqual(len(self.store.folder.load("teardown/consents.json")), 1)
        self.store.upload(self.copy("def456"), "demo", "def456")
        self.assertEqual(len(FakeTerraform.applied), 2)
        self.assertNotEqual(FakeTerraform.applied[0], FakeTerraform.applied[1])


    def reader(self):
        """A store on another computer: the same account, no records of the bucket."""
        other = Store(self.aws, folder=RunFolder(Path(self.tmp.name) / "other-computer"), terraform="terraform")
        other.terraform = lambda: FakeTerraform(other)
        return other

    def test_another_computer_finds_the_bucket_by_its_tags(self):
        self.store.upload(self.copy("abc123"), "demo", "abc123")
        bucket = self.store.bucket
        self.aws.buckets = {bucket: dict(TAGS), "reframe-reports-zzzzzzzz": {"ManagedBy": "someone-else"}}
        self.aws.indexed = [bucket, "reframe-reports-deleted0"]  # the index still lists a deleted bucket
        reader = self.reader()
        self.assertIsNone(reader.bucket)  # no records on this computer
        self.assertEqual([r["run"] for r in reader.reports()], ["abc123"])
        self.assertEqual(reader.download("demo/abc123").read_text(), "<h1>demo abc123</h1>")
        self.assertEqual(reader.inventory(), [])  # nothing to tear down from here: teardown goes by records only
        self.assertEqual(len(FakeTerraform.applied), 1)

    def test_without_the_tag_index_it_lists_buckets_and_reads_their_tags(self):
        self.store.upload(self.copy("abc123"), "demo", "abc123")
        self.aws.buckets = {self.store.bucket: dict(TAGS), "reframe-reports-zzzzzzzz": {"ManagedBy": "someone-else"}}
        self.assertEqual(self.reader().reading_bucket(), self.store.bucket)

    def test_two_buckets_or_none_are_refused(self):
        self.aws.indexed = []
        with self.assertRaisesRegex(Stop, "no Reframe reports bucket"):
            self.reader().reports()
        self.aws.buckets = {"reframe-reports-aaaaaaaa": dict(TAGS), "reframe-reports-bbbbbbbb": dict(TAGS)}
        self.aws.indexed = list(self.aws.buckets)
        with self.assertRaisesRegex(Stop, "more than one"):
            self.reader().reports()


    def test_open_or_download_by_project(self):
        self.store.upload(self.copy("old111", published="2026-11-20T10:00:00Z"), "demo", "old111")
        self.store.upload(self.copy("new222", published="2026-11-23T10:00:00Z"), "demo", "new222")
        self.store.upload(self.copy("other3", "else", "2026-11-24T10:00:00Z"), "else", "other3")
        self.assertEqual(self.store.resolve("demo"), "demo/new222")  # the latest of that project
        self.assertEqual(self.store.resolve("demo", "old111"), "demo/old111")
        self.assertEqual(self.store.resolve("demo/old111/"), "demo/old111")
        with self.assertRaisesRegex(Stop, "nothing is published for project nope; published projects: demo, else"):
            self.store.resolve("nope")
        with self.assertRaisesRegex(Stop, "name the run once"):
            self.store.resolve("demo/old111", "new222")
        target = Path(self.tmp.name) / "for-the-architect"
        page = self.store.download(self.store.resolve("demo"), target)
        self.assertEqual(page, target / "index.html")
        self.assertEqual(page.read_text(), "<h1>demo new222</h1>")
        sync = [c for c in self.aws.calls if c[:2] == ("s3", "sync")][-1]
        self.assertIn("report.zip", sync[sync.index("--exclude") + 1])  # the zip is for the console, not for opening


    def test_each_report_also_has_one_zip_for_the_console(self):
        prefix = self.store.upload(self.copy("abc123"), "demo", "abc123")
        archive = Path(self.tmp.name) / "report.zip"
        archive.write_bytes(self.aws.objects[prefix + "reports/report.zip"])
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(sorted(z.namelist()), ["demo-abc123/aws/index.html", "demo-abc123/index.html", "demo-abc123/manifest.json"])
            self.assertEqual(z.read("demo-abc123/index.html").decode(), "<h1>demo abc123</h1>")
        zip_cp = [c for c in self.aws.calls if c[:2] == ("s3", "cp") and c[3].endswith("report.zip")]
        self.assertEqual(len(zip_cp), 1)
        self.assertIn("--sse", zip_cp[0])
        self.assertEqual([r["run"] for r in self.store.reports()], ["abc123"])  # the zip is not a report of its own

    def run_folder(self):
        folder = RunFolder(Path(self.tmp.name) / ".reframe" / "demo" / "abc123")
        folder.save("snapshot.json", {"archive": "source/archive.zip", "sha256": "1c228d219be10541ab4cd65e", "form": "zip", "files": 3,
                                      "bytes": 9, "source": "/Users/someone/sources/demo.zip"})
        folder.path("source").mkdir(parents=True)
        folder.path("source/archive.zip").write_bytes(b"the code")
        folder.save("outputs/aws/results/manifest.json", {"tool": "aws"})  # the tools' own manifests are not reports
        folder.save("outputs/aws/business-functions/f.json", {"name": "f"})
        folder.path("claude/analysis").mkdir(parents=True)
        folder.path("claude/analysis/summary.md").write_text("# summary")
        return folder

    def test_a_run_is_published_with_its_source_results_and_reports(self):
        copy, run = self.copy("abc123"), self.run_folder()
        prefix = self.store.upload(copy, "demo", "abc123", run)
        self.assertEqual(prefix, "demo/abc123/")
        objects = self.aws.objects
        self.assertEqual(objects["demo/abc123/source/1c228d219be10541.zip"], b"the code")
        note = json.loads(objects["demo/abc123/source/source.json"])
        self.assertEqual((note["sha256"], note["source"]), ("1c228d219be10541ab4cd65e", "demo.zip"))  # no local path
        self.assertIn("demo/abc123/results/aws/business-functions/f.json", objects)
        self.assertEqual(objects["demo/abc123/results/claude/analysis/summary.md"], b"# summary")
        self.assertIn("demo/abc123/reports/index.html", objects)
        self.assertIn("demo/abc123/reports/report.zip", objects)
        self.assertEqual(self.store.sent, ["reports/", "results/", "source/"])
        self.assertTrue(all("--sse" in c for c in self.aws.calls if c[:2] == ("s3", "cp")))
        self.assertEqual([r["path"] for r in self.store.reports()], ["demo/abc123"])
        target = Path(self.tmp.name) / "opened"
        self.assertEqual(self.store.download("demo/abc123", target).read_text(), "<h1>demo abc123</h1>")
        self.assertFalse((target / "results").exists())
        # publishing the same copy again is settled by its receipt: nothing is sent twice
        before = [c for c in self.aws.calls if c[:2] == ("s3", "cp")]
        self.store.upload(copy, "demo", "abc123", run)
        self.assertEqual([c for c in self.aws.calls if c[:2] == ("s3", "cp")], before)

    def test_a_store_whose_terraform_reports_no_bucket_stops(self):
        class NoBucket(FakeTerraform):
            def apply(self, root, changes, backend=None):
                return changes.apply("terraform-reports-apply", "terraform apply", {}, lambda: {"created": [], "outputs": {}})
        self.store.terraform = lambda: NoBucket(self.store)
        with self.assertRaises(Stop) as stop:
            self.store.ensure()
        self.assertIn("did not report its bucket", stop.exception.message)

    def test_reports_published_before_the_three_folders_still_open(self):
        self.store.ensure()
        self.aws.objects.update({"demo/old111/index.html": b"<h1>old</h1>", "demo/old111/report.zip": b"zip",
                                 "demo/old111/manifest.json": json.dumps({"project": "demo", "run": "old111", "published_at": "2026-11-20"}).encode()})
        self.assertEqual([r["path"] for r in self.store.reports()], ["demo/old111"])
        self.assertEqual(self.store.download("demo/old111").read_text(), "<h1>old</h1>")
        # published again in the new layout: listed once, opened from reports/
        self.store.upload(self.copy("old111", published="2026-11-26"), "demo", "old111")
        self.assertEqual([(r["path"], r["published_at"]) for r in self.store.reports()], [("demo/old111", "2026-11-26")])
        self.assertEqual(self.store.download("demo/old111", Path(self.tmp.name) / "again").read_text(), "<h1>demo old111</h1>")


if __name__ == "__main__":
    unittest.main()
