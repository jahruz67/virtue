import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from virtue.host import HostReport, detect_host, find_firmware, parse_vulkaninfo, venus_kernel_requirements


class HostTests(unittest.TestCase):
    def test_secure_capable_firmware_without_keys_is_an_unenrolled_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, variables = root / "CODE.fd", root / "VARS.fd"
            code.write_bytes(b"firmware")
            variables.write_bytes(b"unenrolled")
            descriptor = root / "firmware.json"
            descriptor.write_text(json.dumps({
                "targets": [{"architecture": "x86_64"}], "features": ["secure-boot", "requires-smm"],
                "mapping": {"device": "flash", "executable": {"filename": str(code)},
                            "nvram-template": {"filename": str(variables)}},
            }))
            with patch("virtue.host.glob.glob", return_value=[str(descriptor)]):
                firmware = find_firmware()
            self.assertEqual(firmware["code_plain"], str(code))
            self.assertEqual(firmware["vars_plain"], str(variables))

    def test_non_linux_never_claims_ready(self):
        with patch('virtue.host.host_platform.system', return_value='Windows'), patch('virtue.host.memory_mib', return_value=32768):
            report = detect_host()
        self.assertFalse(report.ready)
        self.assertFalse(report.yttrium_ready)
        self.assertTrue(report.errors)

    def test_cpu_vulkan_is_not_hardware(self):
        text = '''GPU0:
        deviceName = llvmpipe (LLVM 20.1)
        deviceType = PHYSICAL_DEVICE_TYPE_CPU
        apiVersion = 1.3.290
        VK_KHR_external_memory_fd : extension revision 1
        '''
        gpu = parse_vulkaninfo(text)[0]
        self.assertTrue(gpu['software'])
        self.assertFalse(gpu['venus_candidate'])

    def test_hybrid_extension_is_not_applied_to_other_gpu(self):
        text = '''GPU0:
        deviceName = Intel Iris Xe
        deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
        apiVersion = 1.3.290
        GPU1:
        deviceName = AMD Radeon
        deviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
        apiVersion = 1.3.290
        VK_KHR_external_memory_fd : extension revision 1
        '''
        gpus = parse_vulkaninfo(text)
        self.assertEqual(len(gpus), 2)
        self.assertFalse(gpus[0]['venus_candidate'])
        self.assertTrue(gpus[1]['venus_candidate'])

    def test_radv_kernel_requirement(self):
        gpu = {'name':'AMD Radeon', 'driver':'radv', 'type':'PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU', 'venus_candidate':True}
        self.assertFalse(venus_kernel_requirements([gpu], '6.12.5', 'AuthenticAMD')[0])
        ready, pat, _ = venus_kernel_requirements([gpu], '6.13.1', 'AuthenticAMD')
        self.assertTrue(ready)
        self.assertFalse(pat)

    def test_dgpu_on_intel_requires_new_kernel_and_pat(self):
        gpu = {'name':'AMD Radeon', 'driver':'radv', 'type':'PHYSICAL_DEVICE_TYPE_DISCRETE_GPU', 'venus_candidate':True}
        self.assertFalse(venus_kernel_requirements([gpu], '6.15.1', 'GenuineIntel')[0])
        ready, pat, _ = venus_kernel_requirements([gpu], '6.16.1', 'GenuineIntel')
        self.assertTrue(ready)
        self.assertTrue(pat)

    def test_report_json_contains_explicit_checks(self):
        self.assertEqual(HostReport(memory_mib=16384).to_dict()['memory_mib'], 16384)

    def test_iris_xe_marketing_name_does_not_imply_xe_kernel_driver(self):
        gpu = {'name':'Intel Iris Xe Graphics', 'driver':'Intel open-source Mesa', 'kernel_driver':'i915', 'type':'PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU', 'venus_candidate':True}
        ready, pat, _ = venus_kernel_requirements([gpu], '6.8.1', 'GenuineIntel')
        self.assertTrue(ready)
        self.assertFalse(pat)
        gpu['kernel_driver']='xe'
        self.assertFalse(venus_kernel_requirements([gpu], '6.8.1', 'GenuineIntel')[0])


if __name__ == '__main__':
    unittest.main()
