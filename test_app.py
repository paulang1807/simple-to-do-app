import os
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

# Add project root to sys.path
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

class TestAppPaths(unittest.TestCase):

    def test_default_paths(self):
        """Test that server picks up default local paths when no env is set."""
        if "server" in sys.modules:
            del sys.modules["server"]
        
        with patch.dict(os.environ, {}, clear=True):
            import server
            base, data = server.get_app_paths()
            self.assertEqual(base, _HERE.resolve())
            self.assertEqual(data, (_HERE / "data").resolve())

    def test_env_data_dir(self):
        """Test that APP_DATA_DIR environment variable is respected."""
        if "server" in sys.modules:
            del sys.modules["server"]
            
        custom_path = _HERE / "custom_data_test"
        with patch.dict(os.environ, {"APP_DATA_DIR": str(custom_path)}, clear=True):
            import server
            _, data = server.get_app_paths()
            self.assertEqual(data, custom_path.resolve())
            self.assertTrue(custom_path.exists())
            # Cleanup
            custom_path.rmdir()

    def test_dotenv_loading_order(self):
        """Test that .env is loaded from DATA_DIR first, then BASE_DIR."""
        if "server" in sys.modules:
            del sys.modules["server"]
            
        data_dir = _HERE / "test_data_env"
        data_dir.mkdir(exist_ok=True)
        env_file = data_dir / ".env"
        env_file.write_text("TEST_KEY=from_data")
        
        try:
            with patch.dict(os.environ, {"APP_DATA_DIR": str(data_dir)}, clear=True):
                import server
                # server._load_dotenv() is called on import
                self.assertEqual(os.environ.get("TEST_KEY"), "from_data")
        finally:
            env_file.unlink()
            data_dir.rmdir()

class TestBuildInfrastructure(unittest.TestCase):

    def test_build_scripts_exist(self):
        """Verify that automation scripts are present."""
        self.assertTrue((_HERE / "build.sh").exists())
        self.assertTrue((_HERE / "setup_app.py").exists())
        self.assertTrue((_HERE / "app_icon.png").exists())

    def test_build_script_is_executable(self):
        """Verify that build.sh is executable."""
        self.assertTrue(os.access(_HERE / "build.sh", os.X_OK))

if __name__ == "__main__":
    unittest.main()
