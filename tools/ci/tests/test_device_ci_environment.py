"""The QCOM preparation container must share the build runner's cache ownership."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Requires the Linux/macOS workflow shell')
class DeviceCiEnvironmentTests(unittest.TestCase):
  def preparation_script(self):
    workflow = (ROOT / '.github/workflows/build_device.yaml').read_text()
    block = workflow.split('      - name: Prepare source-matched QCOM captures from accepted upstream\n', 1)[1]
    block = block.split('\n      - name:', 1)[0]
    return textwrap.dedent(block.split('        run: |\n', 1)[1])

  def capture_container(self, user_override=None):
    with tempfile.TemporaryDirectory() as directory:
      directory = Path(directory)
      output = directory / 'docker.json'
      for name, body in (
        ('git', "import sys\nif sys.argv[1] == 'rev-parse': print('a' * 40)\n"),
        ('docker', "import json,os,sys\nfrom pathlib import Path\nPath(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"),
      ):
        path = directory / name
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
      env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'],
                 CAPTURE=str(output), COMMA_BUILD_IMAGE='test-builder')
      env.pop('COMMA_DOCKER_RUN_USER', None)
      if user_override is not None:
        env['COMMA_DOCKER_RUN_USER'] = user_override
      subprocess.run(['bash', '-eu', '-c', self.preparation_script()], cwd=directory, env=env, check=True)
      return json.loads(output.read_text())

  def test_preparation_uses_the_same_runner_uid_gid_as_the_build(self):
    args = self.capture_container()
    self.assertEqual(args[args.index('--user') + 1], f'{os.getuid()}:{os.getgid()}')
    self.assertIn('export HOME=/tmp', args[-1])
    self.assertIn('/usr/local/bin/uv sync', args[-1])

  def test_preparation_honors_the_build_user_override(self):
    args = self.capture_container('1234:5678')
    self.assertEqual(args[args.index('--user') + 1], '1234:5678')


if __name__ == '__main__':
  unittest.main()
