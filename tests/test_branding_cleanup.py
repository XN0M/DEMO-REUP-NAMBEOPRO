import copy
from PIL import Image
import unittest
from tests import test_studio_cleanup as cleanup_tests
from core import branding, studio_store as store

class BrandingCleanup(unittest.TestCase):
    def setUp(self):cleanup_tests.CleanupTests.setUp(self)
    def plan(self,mode="compact"):return cleanup_tests.CleanupTests.plan(self,mode)
    def setup_brand(self):
        self.data=store.normalize({'revision':1,'segments':[]})
        self.probe.stop()
        from unittest.mock import patch
        self.probe=patch('core.studio_cleanup.media.probe',return_value={'width':320,'height':240,'duration':1,'fps':20})
        self.probe.start();self.addCleanup(self.probe.stop)
        directory=self.assets/'branding'
        key=branding.put_png(directory,Image.new('RGBA',(20,20),'red'))
        self.data['studio_config']['branding']['logo'].update(enabled=True,image=key)
        return directory,key
    def test_branding_versions_protected_and_orphan_rasters_removed(self):
        directory,key=self.setup_brand()
        versions=self.assets/'versions';versions.mkdir()
        previous=copy.deepcopy(self.data)
        old=branding.put_png(directory,Image.new('RGBA',(20,20),'green'))
        previous['studio_config']['branding']['logo']['image']=old
        store.atomic_json(versions/'00000000.json',previous)
        orphan=branding.put_png(directory,Image.new('RGBA',(20,20),'yellow'))
        library=branding.BrandLibrary(self.root/'library');preset=library.save('Kênh',self.data['studio_config']['branding'],directory)
        self.cleanup.execute(self.plan()['id'],True)
        for identity in (key,old):self.assertTrue(branding.asset_path(directory,identity).exists())
        self.assertFalse(branding.asset_path(directory,orphan).exists())
        self.assertTrue(library.preset_path(preset['id']).exists())
    def test_output_only_removes_project_branding_not_library(self):
        directory,key=self.setup_brand()
        library=branding.BrandLibrary(self.root/'library');preset=library.save('Kênh',self.data['studio_config']['branding'],directory)
        self.cleanup.execute(self.plan('output_only')['id'],True)
        self.assertFalse(branding.asset_path(directory,key).exists())
        self.assertTrue(library.preset_path(preset['id']).exists())
