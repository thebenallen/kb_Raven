# -*- coding: utf-8 -*-
#BEGIN_HEADER
import os
import re
import gzip
import shutil
import subprocess
import uuid
from typing import Dict, List

from installed_clients.WorkspaceClient import Workspace
from installed_clients.ReadsUtilsClient import ReadsUtils
from installed_clients.AssemblyUtilClient import AssemblyUtil
from installed_clients.KBaseReportClient import KBaseReport
#END_HEADER


class kb_raven:
    '''
    Module Name:
    kb_raven

    Module Description:
    A KBase module for running the Raven long-read assembler.
    '''

    VERSION = "0.0.1"
    GIT_URL = "https://github.com/your_org/kb_raven"
    GIT_COMMIT_HASH = "local-dev"

    ######## WARNING FOR GEVENT USERS #######
    # Since asynchronous IO can lead to methods - even the same method -
    # interrupting each other, you must be very careful when using global
    # state. A method could be interrupted by another method while it is
    # running, and then be resumed later.

    #BEGIN_CLASS_HEADER
    # Hardwired runtime defaults to keep the UI simple and reproducible.
    DEFAULT_THREADS = max(1, min(16, os.cpu_count() or 1))
    DEFAULT_POLISHING_ROUNDS = 2
    DEFAULT_MIN_UNITIG_SIZE = 9999
    DEFAULT_GFA_FILENAME = "assembly_graph.gfa"
    DEFAULT_FASTA_FILENAME = "assembly.fasta"
    DEFAULT_LOG_FILENAME = "raven.log"
    DEFAULT_STDOUT_FILENAME = "raven.stdout.fasta"

    def _validate_params(self, params: Dict):
        required = ['reads_ref', 'output_assembly_name', 'workspace_name']
        missing = [k for k in required if not params.get(k)]
        if missing:
            raise ValueError('Missing required parameter(s): {}'.format(', '.join(missing)))

    def _to_bool(self, value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in {'1', 'true', 'yes'}

    def _stage_reads(self, run_dir: str, reads_ref: str) -> List[str]:
        obj_info = self.ws.get_object_info3({'objects': [{'ref': reads_ref}]})['infos'][0]
        obj_type = obj_info[2].split('-')[0]

        if obj_type == 'KBaseSets.ReadsSet':
            members = self._extract_reads_set_members(reads_ref)
            staged = []
            for i, member_ref in enumerate(members, start=1):
                staged.extend(
                    self._download_reads_object(
                        member_ref,
                        os.path.join(run_dir, 'reads_{}'.format(i))
                    )
                )
            if not staged:
                raise ValueError('ReadsSet contained no readable members: {}'.format(reads_ref))
            return staged

        return self._download_reads_object(reads_ref, os.path.join(run_dir, 'reads_1'))

    def _extract_reads_set_members(self, reads_set_ref: str) -> List[str]:
        data = self.ws.get_objects2({'objects': [{'ref': reads_set_ref}]})['data'][0]['data']
        items = data.get('items', [])
        refs = []
        for item in items:
            if isinstance(item, dict):
                ref = item.get('ref') or item.get('item_ref')
                if ref:
                    refs.append(ref)
        return refs

    def _download_reads_object(self, reads_ref: str, target_dir: str) -> List[str]:
        os.makedirs(target_dir, exist_ok=True)
        out = self.ru.download_reads({
            'read_libraries': [reads_ref],
            'interleaved': 'false'
        })

        top = out['files'][reads_ref]

        # ReadsUtils nests actual file paths under top['files']
        inner = top.get('files', {})

        paths = []
        for key in ('fwd', 'rev'):
            value = inner.get(key)
            if isinstance(value, str) and os.path.exists(value):
                paths.append(self._materialize_input(value, target_dir))

        deduped = []
        for path in paths:
            if path not in deduped:
                deduped.append(path)

        if not deduped:
            raise ValueError('Unable to resolve reads files for {}. inner keys: {}'.format(
                reads_ref, list(inner.keys())
            ))

        return deduped


    def _materialize_input(self, src_path: str, target_dir: str) -> str:
        basename = os.path.basename(src_path)

        if basename.endswith('.gz'):
            out_path = os.path.join(target_dir, re.sub(r'\.gz$', '', basename))
            with gzip.open(src_path, 'rb') as src, open(out_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            return out_path

        out_path = os.path.join(target_dir, basename)
        if os.path.abspath(src_path) != os.path.abspath(out_path):
            shutil.copy2(src_path, out_path)
        return out_path

    def _normalize_fasta(self, src_path: str, dst_path: str):
        """
        Copy only valid FASTA content - header lines and nucleotide sequence lines.
        Skips blank lines, GFA lines (S/H/L/P/W), and any non-FASTA content.
        """
        valid_bases = set('ACGTNacgtn')
        written = 0

        with open(src_path, 'r') as src, open(dst_path, 'w') as dst:
            current_header = None
            current_seq_lines = []

            for line in src:
                line_stripped = line.strip()
                if not line_stripped:
                    continue

                if line_stripped.startswith('>'):
                    # Flush previous contig if it had sequence
                    if current_header and current_seq_lines:
                        dst.write(current_header + '\n')
                        for sl in current_seq_lines:
                            dst.write(sl + '\n')
                        written += 1
                    current_header = line_stripped
                    current_seq_lines = []

                elif current_header is not None:
                    # Validate it looks like a sequence line
                    cleaned = line_stripped.upper()
                    if all(c in valid_bases for c in cleaned):
                        current_seq_lines.append(line_stripped)
                    # else skip contaminated line silently

            # Flush last contig
            if current_header and current_seq_lines:
                dst.write(current_header + '\n')
                for sl in current_seq_lines:
                    dst.write(sl + '\n')
                written += 1

        if written == 0:
            raise ValueError(
                'No valid FASTA contigs found in Raven output: {}'.format(src_path)
            )

        print('_normalize_fasta: wrote {} contigs to {}'.format(written, dst_path))

    def _compute_fasta_stats(self, fasta_path: str) -> Dict[str, int]:
        lengths = []
        seq_len = 0

        with open(fasta_path, 'r') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('>'):
                    if seq_len:
                        lengths.append(seq_len)
                    seq_len = 0
                else:
                    seq_len += len(line)

            if seq_len:
                lengths.append(seq_len)

        if not lengths:
            raise ValueError('No contigs found in Raven FASTA output')

        lengths.sort(reverse=True)
        total = sum(lengths)
        running = 0
        n50 = 0

        for length in lengths:
            running += length
            if running >= total / 2:
                n50 = length
                break

        return {
            'contig_count': len(lengths),
            'total_length': total,
            'max_length': lengths[0],
            'n50': n50
        }

    def _build_report_text(self, assembly_name: str, assembly_ref: str, stats: Dict[str, int], emit_gfa: bool) -> str:
        gfa_text = 'yes' if emit_gfa else 'no'
        return (
            'Raven assembly completed successfully.\n\n'
            'Assembly object: {}\n'
            'Assembly ref: {}\n'
            'Contigs: {}\n'
            'Total length: {} bp\n'
            'Max contig: {} bp\n'
            'N50: {} bp\n'
            'GFA exported: {}\n'
            'Threads: {}\n'
            'Polishing rounds: {}\n'
            'Min unitig size: {}\n'.format(
                assembly_name,
                assembly_ref,
                stats['contig_count'],
                stats['total_length'],
                stats['max_length'],
                stats['n50'],
                gfa_text,
                self.DEFAULT_THREADS,
                self.DEFAULT_POLISHING_ROUNDS,
                self.DEFAULT_MIN_UNITIG_SIZE
            )
        )
    #END_CLASS_HEADER

    def __init__(self, config):
        #BEGIN_CONSTRUCTOR
        self.config = config
        self.scratch = config['scratch']
        self.callback_url = os.environ['SDK_CALLBACK_URL']
        self.ws = Workspace(config['workspace-url'])
        self.ru = ReadsUtils(self.callback_url)
        self.au = AssemblyUtil(self.callback_url)
        self.report = KBaseReport(self.callback_url)
        #END_CONSTRUCTOR
        pass

    def run_kb_raven(self, ctx, params):
        #BEGIN run_kb_raven
        self._validate_params(params)

        workspace_name = params['workspace_name']
        reads_ref = params['reads_ref']
        emit_gfa = self._to_bool(params.get('emit_gfa', 1))

        run_dir = os.path.join(self.scratch, 'raven_' + uuid.uuid4().hex)
        os.makedirs(run_dir, exist_ok=True)

        input_files = self._stage_reads(run_dir, reads_ref)
        raven_stdout = os.path.join(run_dir, self.DEFAULT_STDOUT_FILENAME)
        raven_log = os.path.join(run_dir, self.DEFAULT_LOG_FILENAME)
        gfa_path = os.path.join(run_dir, self.DEFAULT_GFA_FILENAME)
        fasta_path = os.path.join(run_dir, self.DEFAULT_FASTA_FILENAME)

        cmd = [
            'raven',
            '-t', str(self.DEFAULT_THREADS),
            '-p', str(self.DEFAULT_POLISHING_ROUNDS),
            '-u', str(self.DEFAULT_MIN_UNITIG_SIZE),
        ]
        if emit_gfa:
            cmd.extend(['--graphical-fragment-assembly', gfa_path])
        cmd.extend(input_files)

        with open(raven_stdout, 'w') as stdout_handle, open(raven_log, 'w') as log_handle:
            log_handle.write('Command:\n{}\n\n'.format(' '.join(cmd)))
            completed = subprocess.run(
                cmd,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                cwd=run_dir,
                check=False,
                text=True
            )
            log_handle.write('Return code: {}\n'.format(completed.returncode))

        if completed.returncode != 0:
            raise ValueError('Raven failed. See log file in scratch: {}'.format(raven_log))

        self._normalize_fasta(raven_stdout, fasta_path)
        stats = self._compute_fasta_stats(fasta_path)

        assembly_ref = self.au.save_assembly_from_fasta({
            'file': {'path': fasta_path},
            'workspace_name': workspace_name,
            'assembly_name': params['output_assembly_name']
        })

        file_links = [
            {
                'path': fasta_path,
                'name': self.DEFAULT_FASTA_FILENAME,
                'label': 'Raven assembly FASTA'
            },
            {
                'path': raven_log,
                'name': self.DEFAULT_LOG_FILENAME,
                'label': 'Raven log'
            }
        ]

        if emit_gfa and os.path.exists(gfa_path):
            file_links.append({
                'path': gfa_path,
                'name': self.DEFAULT_GFA_FILENAME,
                'label': 'Raven assembly graph (GFA)'
            })

        text_message = self._build_report_text(
            params['output_assembly_name'],
            assembly_ref,
            stats,
            emit_gfa
        )

        report_info = self.report.create_extended_report({
            'message': text_message,
            'file_links': file_links,
            'objects_created': [{
                'ref': assembly_ref,
                'description': 'Assembly generated by Raven'
            }],
            'workspace_name': workspace_name,
            'report_object_name': 'kb_raven_report_' + uuid.uuid4().hex
        })

        return [{
            'report_name': report_info['name'],
            'report_ref': report_info['ref']
        }]
        #END run_kb_raven

    def status(self, ctx):
        return [{'state': "OK",
                 'message': "",
                 'version': self.VERSION,
                 'git_url': self.GIT_URL,
                 'git_commit_hash': self.GIT_COMMIT_HASH}]
