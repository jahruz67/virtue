import unittest

from virtue.graphics import classify_guest_report


class GraphicsTests(unittest.TestCase):
    def test_rejects_unknown_schema(self):
        with self.assertRaises(ValueError):
            classify_guest_report({})

    def test_rejects_invalid_nested_report(self):
        with self.assertRaises(ValueError):
            classify_guest_report({'schema_version':1, 'render_tests':[1]})

    def test_enumeration_is_not_rendering(self):
        report = {'schema_version':1, 'vulkan':{'devices':[{'name':'Virtio-GPU Venus (Intel Iris Xe)'}]}}
        self.assertEqual(classify_guest_report(report)['state'], 'driver_detected_unverified')

    def test_gl_draw_identifies_path_but_not_cad(self):
        report = {'schema_version':1, 'opengl':{'renderer':'zink Vulkan 1.3 (Virtio-GPU Venus (Intel Iris Xe))','vendor':'Mesa','draw_test_passed':True}}
        result = classify_guest_report(report)
        self.assertEqual(result['state'], 'hardware_rendering_observed')
        self.assertIn('Unverified', result['evidence']['direct3d'])

    def test_zink_on_lavapipe_does_not_count_as_hardware(self):
        report = {'schema_version':1, 'opengl':{'renderer':'zink Vulkan 1.3 (Virtio-GPU Venus (llvmpipe))','draw_test_passed':True}}
        self.assertEqual(classify_guest_report(report)['state'], 'software_fallback')

    def test_d3d_presence_not_enough(self):
        report = {'schema_version':1, 'driver_installed':True, 'direct3d':{'feature_level':'11_0'}}
        self.assertEqual(classify_guest_report(report)['state'], 'driver_not_functioning')

    def test_safe_display_generic_renderer(self):
        report = {'schema_version':1, 'opengl':{'renderer':'GDI Generic','draw_test_passed':True}}
        self.assertEqual(classify_guest_report(report)['state'], 'software_fallback')


if __name__ == '__main__':
    unittest.main()
