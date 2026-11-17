import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from deploy.desktop_manager import desktop_session as desktop


class DesktopSessionTests(unittest.TestCase):
    def test_session_id_reports_current_process_session(self):
        self.assertIsInstance(desktop.session_id(os.getpid()), int)

    def test_grant_preserves_existing_aces_and_is_idempotent(self):
        acl = Mock()
        acl.GetAceCount.return_value = 1
        acl.GetAce.return_value = ((desktop.win32con.ACCESS_ALLOWED_ACE_TYPE, 0), 7, "other")
        info = Mock()
        info.GetSecurityDescriptorDacl.return_value = acl
        with patch.object(desktop.win32security, "GetSecurityInfo", return_value=info), \
             patch.object(desktop.win32security, "SetSecurityInfo") as write:
            desktop._grant("object", "fish", 3)
            acl.AddAccessAllowedAceEx.assert_called_once_with(desktop.win32security.ACL_REVISION, 0, 3, "fish")
            self.assertIs(write.call_args.args[5], acl)
            acl.GetAce.return_value = ((desktop.win32con.ACCESS_ALLOWED_ACE_TYPE, 0), 7, "fish")
            desktop._grant("object", "fish", 3)
            self.assertEqual(write.call_count, 1)

    def test_null_dacl_is_not_replaced(self):
        info = Mock()
        info.GetSecurityDescriptorDacl.return_value = None
        with patch.object(desktop.win32security, "GetSecurityInfo", return_value=info), \
             patch.object(desktop.win32security, "SetSecurityInfo") as write:
            desktop._grant("object", "fish", 3)
            write.assert_not_called()

    def test_guard_precedes_grant_and_handles_close_on_failure(self):
        station, desk = Mock(), Mock()
        with patch.object(desktop, "get_session"), \
             patch.object(desktop.win32security, "LookupAccountName", return_value=("sid", None, None)), \
             patch.object(desktop.win32service, "OpenWindowStation", return_value=station), \
             patch.object(desktop.win32service, "OpenDesktop", return_value=desk), \
             patch.object(desktop, "_grant", side_effect=[None, RuntimeError("denied")]):
            with self.assertRaisesRegex(RuntimeError, "denied"):
                with desktop.desktop_access("fish"):
                    self.fail("must not start")
        desk.CloseDesktop.assert_called_once()
        station.CloseWindowStation.assert_called_once()

    def test_launch_passes_credentials_only_to_process_creation(self):
        @contextmanager
        def access(account):
            self.assertEqual(account, "fish")
            yield
        with patch.object(desktop, "desktop_access", access), patch.object(desktop, "run_as", return_value=42) as run:
            self.assertEqual(desktop.launch_desktop("fish", "secret", ["broker"], {"A": "B"}), 42)
            run.assert_called_once_with("fish", "secret", ["broker"], env={"A": "B"})

    def test_guard_failure_prevents_launch(self):
        with patch.object(desktop, "get_session", side_effect=RuntimeError("locked")), \
             patch.object(desktop, "run_as") as run:
            with self.assertRaisesRegex(RuntimeError, "locked"):
                desktop.launch_desktop("fish", "secret", ["broker"], {})
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
