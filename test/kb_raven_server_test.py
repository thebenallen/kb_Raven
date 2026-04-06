# -*- coding: utf-8 -*-
import os
import time
import unittest
from configparser import ConfigParser
from kb_raven.kb_ravenImpl import kb_raven
from kb_raven.kb_ravenServer import MethodContext
from kb_raven.authclient import KBaseAuth as _KBaseAuth
from installed_clients.WorkspaceClient import Workspace
from installed_clients.ReadsUtilsClient import ReadsUtils

class kb_ravenTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        token = os.environ.get('KB_AUTH_TOKEN', None)
        config_file = os.environ.get('KB_DEPLOYMENT_CONFIG', None)
        cls.cfg = {}
        config = ConfigParser()
        config.read(config_file)
        for nameval in config.items('kb_raven'):
            cls.cfg[nameval[0]] = nameval[1]

        authServiceUrl = cls.cfg['auth-service-url']
        auth_client = _KBaseAuth(authServiceUrl)
        user_id = auth_client.get_user(token)

        cls.ctx = MethodContext(None)
        cls.ctx.update({'token': token,
                        'user_id': user_id,
                        'provenance': [
                            {'service': 'kb_raven',
                             'method': 'please_never_use_it_in_production',
                             'method_params': []
                             }],
                        'authenticated': 1})

        cls.wsURL = cls.cfg['workspace-url']
        cls.wsClient = Workspace(cls.wsURL, token=token)
        cls.serviceImpl = kb_raven(cls.cfg)
        cls.scratch = cls.cfg['scratch']
        cls.callback_url = os.environ['SDK_CALLBACK_URL']

        suffix = int(time.time() * 1000)
        cls.wsName = "test_kb_raven_" + str(suffix)
        ret = cls.wsClient.create_workspace({'workspace': cls.wsName})
        cls.wsId = ret[0]

        # Copy your reads object from narrative workspace into test workspace
        cls.reads_ref = cls._copy_reads_to_test_ws(
            cls,
            source_ref='77204/2/1'
        )

    def _copy_reads_to_test_ws(self, source_ref):
        """Copy reads object from narrative workspace to test workspace."""
        print('\nCopying reads object {} to test workspace...'.format(source_ref))
        ret = self.wsClient.copy_object({
            'from': {'ref': source_ref},
            'to': {
                'workspace': self.wsName,
                'name': 'test_reads'
            }
        })
        # ret is [obj_id, obj_name, obj_type, ...]
        new_ref = '{}/{}'.format(self.wsId, ret[0])
        print('Reads copied to: {}'.format(new_ref))
        return new_ref

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'wsName'):
            cls.wsClient.delete_workspace({'workspace': cls.wsName})
            print('Test workspace was deleted')

    def test_run_raven(self):
        print('\n=== Running Raven assembler ===')
        print('Using reads ref: {}'.format(self.reads_ref))

        ret = self.serviceImpl.run_kb_raven(self.ctx, {
            'workspace_name': self.wsName,
            'reads_ref': self.reads_ref,
            'output_assembly_name': 'test_raven_assembly'
        })

        print('Result: {}'.format(ret))
        self.assertIsNotNone(ret)
        self.assertIsInstance(ret, list)
        self.assertIn('report_ref', ret[0])
        self.assertIn('report_name', ret[0])
        print('=== Test passed ===')

    def test_debug_reads(self):
        """Diagnostic test — run this first to verify reads object is accessible."""
        print('\n=== DEBUG: checking reads object ===')

        source_ref = '77204/2/1'
        obj_info = self.wsClient.get_object_info3({
            'objects': [{'ref': source_ref}]
        })
        info = obj_info['infos'][0]
        print('Object name: {}'.format(info[1]))
        print('Object type: {}'.format(info[2]))
        print('Workspace ID: {}'.format(info[6]))
        print('Object ID: {}'.format(info[0]))

        # Verify the copied ref is also accessible
        obj_info2 = self.wsClient.get_object_info3({
            'objects': [{'ref': self.reads_ref}]
        })
        info2 = obj_info2['infos'][0]
        print('Copied object name: {}'.format(info2[1]))
        print('Copied object type: {}'.format(info2[2]))
        print('Test workspace ref: {}'.format(self.reads_ref))
        print('=== DEBUG complete ===')
