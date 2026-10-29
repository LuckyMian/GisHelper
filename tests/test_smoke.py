import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from gismapshelper import __version__
from gismapshelper.app import describe_environment, environment_rows, main


class SmokeTest(unittest.TestCase):
    def test_version_format(self) -> None:
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_environment_rows(self) -> None:
        rows = dict(environment_rows())
        self.assertIn("Python", rows)
        self.assertIn("解释器", rows)

    def test_describe_environment(self) -> None:
        report = describe_environment()
        self.assertIn("Python", report)

    def test_env_option_prints_and_returns_zero(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--env"]), 0)
        self.assertIn("虚拟环境", buf.getvalue())

    def test_version_option(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--version"]), 0)
        self.assertEqual(buf.getvalue().strip(), __version__)

    def test_inspect_requires_argument(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["--inspect"]), 2)
        self.assertIn("--inspect", err.getvalue())

    def test_inspect_missing_file(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["--inspect", "不存在的文件.tif"]), 1)
        self.assertIn("读取失败", err.getvalue())


if __name__ == "__main__":
    unittest.main()
