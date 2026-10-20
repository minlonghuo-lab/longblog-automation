import argparse
import contextlib
import copy
import io
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from article_metadata import normalize_post_metadata, note_url_identifier, summarize_html, manual_summary
from legacy_attributes import cleanup_legacy_attributes
import poll_trilium_changes as poll
import sync_trilium_posts as sync


def note(note_id, **fields):
    return {"noteId": note_id, "title": "文章 " + note_id, "type": "text", "attributes": [
        {"attributeId": note_id + key, "noteId": note_id, "type": "label", "name": key, "value": value}
        for key, value in fields.items()
    ]}


def state(note_id, **fields):
    return {"id": note_id, "title": note_id, "publish": True, "sync": False, **fields}


class SummaryTests(unittest.TestCase):
    def test_extracts_prose_ignoring_code_diagrams_headings_and_tables(self):
        body = ('<h2>标题不能当摘要</h2><pre><code>flowchart TD A--&gt;B</code></pre>'
                '<table><tr><td>表格内容不能当摘要</td></tr></table>'
                '<script>alert(1)</script><p>这篇文章记录博客自动发布流程，包含正文转换与附件管理。</p>')
        self.assertEqual(summarize_html(body), '这篇文章记录博客自动发布流程，包含正文转换与附件管理。')

    def test_html_entities_and_inline_code_are_readable(self):
        self.assertEqual(summarize_html('<p>使用 <code>Docker</code> &amp; GitHub，让文章的更新流程更简单。</p>'),
                         '使用 Docker & GitHub，让文章的更新流程更简单。')

    def test_summary_is_bounded_and_falls_back_for_image_only_posts(self):
        self.assertLessEqual(len(summarize_html('<p>' + '正文测试' * 100 + '</p>')), 80)
        self.assertEqual(summarize_html('<figure><img src="x"></figure>', '照片'), '一起读读《照片》。')
        sentence = '这篇文章记录博客自动发布流程，包含正文转换与附件管理。'
        self.assertEqual(summarize_html('<p>' + sentence + '其他文字' * 100 + '</p>'), sentence)

    def test_chinese_label_is_the_only_mascot_summary_and_preserves_author_text(self):
        text = '手写摘要。\n' + '不截断作者文字。' * 30
        article = note('A', summary='旧的英文摘要', **{'摘要': text})
        self.assertEqual(manual_summary(article), text)
        self.assertEqual(manual_summary(note('A', summary='旧值')), '')
        updated = normalize_post_metadata({'id': 'A', 'manualSummary': text, 'contentHtml': '<p>正文。</p>'})
        self.assertEqual(updated['manualSummary'], text)
        self.assertEqual(updated['summary'], text)
        cleared = normalize_post_metadata({**updated, 'manualSummary': ''})
        self.assertEqual(cleared['manualSummary'], '')
        self.assertNotEqual(cleared['summary'], text)

    def test_cached_metadata_is_rebuilt_without_old_fields(self):
        old = {'id': 'xtusIYhPQyK5', 'slug': '旧标题', 'title': '文章', 'summary': '旧 AI 摘要', 'tags': ['旧标签'], 'pinned': True,
               'contentHtml': '<p>这是一段已发布的正文，用来重新生成简短摘要。</p>', 'publishedAt': '2025-01-01'}
        new = normalize_post_metadata(old)
        self.assertEqual(new['summary'], '这是一段已发布的正文，用来重新生成简短摘要。')
        self.assertEqual(new['tags'], [])
        self.assertNotIn('pinned', new)
        self.assertEqual(new['slug'], 'xtusIYhPQyK5')
        self.assertEqual(old['summary'], '旧 AI 摘要')
        self.assertEqual(new['publishedAt'], old['publishedAt'])


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backup = Path(self.temp.name) / 'state' / 'backup.json'

    def test_cleans_values_and_promoted_or_copied_definitions_only_in_owned_scope(self):
        article = note('A', tags='旧标签', summary='旧摘要', aiRefresh='true', pinned='true',
                       **{'label:summary': 'promoted', 'child:label:tags': 'promoted', 'publish': 'true'})
        article['attributes'] += [
            {'attributeId': 'external', 'noteId': 'outside', 'type': 'label', 'name': 'tags'},
            {'attributeId': 'relation', 'noteId': 'A', 'type': 'relation', 'name': 'summary'},
        ]
        deleted = []
        def remove(attribute_id):
            self.assertIn(attribute_id, json.loads(self.backup.read_text()))
            deleted.append(attribute_id)
        self.assertEqual(cleanup_legacy_attributes([article], remove, self.backup), 6)
        self.assertEqual(len(deleted), 6)
        self.assertNotIn('external', deleted)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cleanup_legacy_attributes([note('A', publish='true')], remove, self.backup), 0)

    def test_chinese_summary_and_template_definitions_are_never_retired(self):
        article = note('A', summary='旧字段', **{'摘要': '我的摘要', 'label:摘要': 'promoted', 'child:label:摘要': 'promoted'})
        deleted = Mock()
        self.assertEqual(cleanup_legacy_attributes([article], deleted, self.backup), 1)
        deleted.assert_called_once_with('Asummary')

    def test_failed_cleanup_keeps_backup_and_can_retry(self):
        with self.assertRaises(RuntimeError):
            cleanup_legacy_attributes([note('A', summary='旧摘要')], Mock(side_effect=RuntimeError()), self.backup)
        saved = json.loads(self.backup.read_text())
        cleanup_legacy_attributes([note('B', tags='旧标签')], Mock(), self.backup)
        self.assertEqual(json.loads(self.backup.read_text())['Asummary'], saved['Asummary'])

    def test_corrupt_backup_aborts_before_delete(self):
        self.backup.parent.mkdir()
        self.backup.write_text('bad json')
        delete = Mock()
        with self.assertRaises(json.JSONDecodeError):
            cleanup_legacy_attributes([note('A', summary='value')], delete, self.backup)
        delete.assert_not_called()


class PollerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        values = {'BASE_URL': 'http://test', 'TOKEN': 'test', 'ROOT_NOTE_ID': 'root',
                  'STATE_DIR': root, 'LOG_DIR': root, 'SNAPSHOT_FILE': root / 'snapshot.json',
                  'POLICY_FILE': root / 'policy.json', 'CTX_FILE': root / 'event.json',
                  'LOCK_FILE': root / 'poll.lock', 'SUCCESS_FILE': root / 'success', 'LOG_FILE': root / 'poll.log'}
        for key, value in values.items():
            item = patch.object(poll, key, value)
            item.start()
            self.addCleanup(item.stop)

    def test_retired_labels_draft_edits_and_acknowledgements_do_not_publish(self):
        old = {'A': state('A', pinned=True, aiRefresh=True, contentHash='old')}
        new = {'A': state('A', title='新草稿标题', contentHash='new')}
        self.assertEqual(poll.differences(old, new), [])
        self.assertEqual(poll.differences({'A': state('A', sync=True)}, new), [])

    def test_summary_edit_remains_explicitly_published_through_sync(self):
        old = {'A': state('A', manualSummary='原摘要')}
        current = {'A': state('A', manualSummary='新摘要')}
        self.assertEqual(poll.differences(old, current), [])
        current['A']['sync'] = True
        self.assertEqual(poll.differences(old, current)[0]['changed'], ['sync'])

    def test_publish_sync_and_removal_still_trigger(self):
        old = {'A': state('A')}
        for current in [state('A', sync=True), state('A', publish=False)]:
            self.assertEqual(len(poll.differences(old, {'A': current})), 1)
        self.assertEqual(poll.differences(old, {})[0]['type'], 'removed')
        self.assertEqual(len(poll.differences({}, {'A': state('A')})), 1)
        self.assertEqual(poll.differences({}, {'A': state('A', publish=False, sync=True)}), [])

    def test_snapshot_never_downloads_article_bodies(self):
        with patch.object(poll, 'walk', return_value=[note('A', publish='true')]), patch.object(poll, 'get') as get:
            self.assertEqual(poll.snapshot()['A']['publish'], True)
            get.assert_not_called()

    def test_multiple_changes_run_one_batch_and_success_clears_intent(self):
        old = {'A': state('A'), 'B': state('B')}
        current = {key: {**value, 'sync': True} for key, value in old.items()}
        poll.write_json(poll.SNAPSHOT_FILE, old)
        poll.write_json(poll.POLICY_FILE, {'version': poll.POLICY_VERSION})
        with patch.object(poll, 'snapshot', side_effect=[current, old]), patch.object(poll.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(poll.main(), 0)
        run.assert_called_once()
        self.assertFalse(poll.CTX_FILE.exists())
        self.assertEqual(poll.read_json(poll.SNAPSHOT_FILE), old)

    def test_failed_build_retries_acknowledged_request_and_merges_new_requests(self):
        old = {'A': state('A'), 'B': state('B')}
        poll.write_json(poll.SNAPSHOT_FILE, old)
        poll.write_json(poll.POLICY_FILE, {'version': poll.POLICY_VERSION})
        first = {**old, 'A': state('A', sync=True)}
        with patch.object(poll, 'snapshot', return_value=first), patch.object(poll.subprocess, 'run', return_value=Mock(returncode=1)):
            self.assertEqual(poll.main(), 1)
        self.assertEqual(poll.read_json(poll.CTX_FILE)['noteIds'], ['A'])
        later = {**old, 'B': state('B', sync=True)}
        def retry(*args, **kwargs):
            self.assertEqual(poll.read_json(poll.CTX_FILE)['noteIds'], ['A', 'B'])
            return Mock(returncode=0)
        with patch.object(poll, 'snapshot', side_effect=[later, old]), patch.object(poll.subprocess, 'run', side_effect=retry):
            self.assertEqual(poll.main(), 0)
        self.assertFalse(poll.CTX_FILE.exists())

    def test_request_arriving_during_build_is_not_acknowledged(self):
        old = {'A': state('A'), 'B': state('B')}
        current = {**old, 'A': state('A', sync=True)}
        after = {**old, 'B': state('B', sync=True)}
        poll.write_json(poll.SNAPSHOT_FILE, old)
        poll.write_json(poll.POLICY_FILE, {'version': poll.POLICY_VERSION})
        with patch.object(poll, 'snapshot', side_effect=[current, after]), patch.object(poll.subprocess, 'run', return_value=Mock(returncode=0)):
            self.assertEqual(poll.main(), 0)
        baseline = poll.read_json(poll.SNAPSHOT_FILE)
        changes = poll.differences(baseline, after)
        self.assertEqual([change['note']['id'] for change in changes], ['B'])

    def test_new_sync_or_unpublish_on_target_during_build_remains_pending(self):
        for after in [state('A', sync=True), state('A', publish=False)]:
            with self.subTest(after=after):
                old = {'A': state('A')}
                poll.write_json(poll.SNAPSHOT_FILE, old)
                poll.write_json(poll.POLICY_FILE, {'version': poll.POLICY_VERSION})
                with patch.object(poll, 'snapshot', side_effect=[{'A': state('A', sync=True)}, {'A': after}]), patch.object(poll.subprocess, 'run', return_value=Mock(returncode=0)):
                    self.assertEqual(poll.main(), 0)
                self.assertEqual(len(poll.differences(poll.read_json(poll.SNAPSHOT_FILE), {'A': after})), 1)

    def test_upgrade_runs_once_and_first_install_only_initializes(self):
        old = {'A': state('A', pinned=True, aiRefresh=True)}
        with patch.object(poll, 'snapshot', return_value=old), patch.object(poll.subprocess, 'run') as run:
            self.assertEqual(poll.main(), 0)
            run.assert_not_called()
        poll.write_json(poll.POLICY_FILE, {"version": 2})
        with patch.object(poll, 'snapshot', return_value=old), patch.object(poll.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(poll.main(), 0)
            run.assert_called_once()
        with patch.object(poll, 'snapshot', return_value=old), patch.object(poll.subprocess, 'run') as run:
            self.assertEqual(poll.main(), 0)
            run.assert_not_called()


class SyncIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {'BASE_URL': 'http://test', 'TOKEN': 'test', 'ROOT_NOTE_ID': 'root', 'TEMPLATE_NOTE_ID': 'template',
                  'BASE_DIR': str(self.root), 'OUT_CONTENT_TS': str(self.root / 'content.ts'),
                  'OUT_META_TS': str(self.root / 'meta.ts'), 'ASSET_DIR': str(self.root / 'assets'),
                  'PUBLISHED_AT_REGISTRY': str(self.root / 'state' / 'dates.json')}
        for key, value in values.items():
            item = patch.object(sync, key, value)
            item.start()
            self.addCleanup(item.stop)
        self.body = '<p>这是已经发布的正文，自动提取出简短摘要供小宠物展示。</p>'
        self.posts = [{"id": key, "title": "文章 " + key, "slug": '旧标题-' + key, "contentHtml": self.body,
                       "summary": "旧摘要", "tags": ["旧标签"], "pinned": True, "publishedAt": '2025-01-01',
                       "updatedAt": '2025-01-02', "syncHash": "old", "syncStatus": "published"} for key in ['A', 'B']]
        sync.write_generated_files(self.posts)
        self.notes = [note('root'), note('A', publish='true', summary='旧摘要', slug='旧标题-A'), note('B', publish='true', slug='旧标题-B'),
                      note('template', **{'label:tags': 'promoted', 'label:summary': 'promoted', 'label:pinned': 'promoted', 'label:aiRefresh': 'promoted'})]

    def execute(self, event='metadata_migrated', ids=(), fail=None):
        by_id = {item['noteId']: item for item in self.notes}
        def get(path):
            return copy.deepcopy(by_id[path.split('/')[3]])
        def set_label(note_id, name, value):
            attrs = by_id[note_id]['attributes']
            attrs[:] = [attr for attr in attrs if attr['name'] != name]
            attrs.append({'attributeId': note_id + name, 'noteId': note_id, 'type': 'label', 'name': name, 'value': value})
        def delete(path):
            for item in self.notes:
                item['attributes'] = [attr for attr in item['attributes'] if attr['attributeId'] != path.rsplit('/', 1)[1]]
        args = argparse.Namespace(requestId='test', event=event, noteId='', noteIds=list(ids))
        output = io.StringIO()
        with patch.object(sync, 'parse_args', return_value=args), patch.object(sync, 'get_json', side_effect=get), \
             patch.object(sync, 'walk_note_tree', return_value=self.notes), patch.object(sync, 'delete_path', side_effect=delete), \
             patch.object(sync, 'set_label', side_effect=set_label) as label, patch.object(sync, 'get_text', side_effect=fail, return_value=self.body) as content, \
             patch.object(sync, 'localize_attachments', side_effect=lambda html, note_id: (html, [], False)), \
             patch('subprocess.run') as process, contextlib.redirect_stdout(output):
            result = sync.main()
        process.assert_not_called()  # conversion never invokes models or Git
        return result, json.loads(output.getvalue()), label, content

    def test_migration_uses_published_cache_and_preserves_dates(self):
        result, report, labels, content = self.execute()
        self.assertEqual(result, 0)
        self.assertEqual(report['retiredAttributesRemoved'], 5)
        content.assert_not_called()
        self.assertEqual(sync.load_existing_content_records()[0]['summary'], '这是已经发布的正文，自动提取出简短摘要供小宠物展示。')
        for item in sync.load_existing_meta_records():
            self.assertEqual(item['slug'], item['id'])
            self.assertEqual(item['tags'], [])
            self.assertNotIn('pinned', item)
            self.assertEqual(item['publishedAt'], '2025-01-01')
            self.assertEqual(item['updatedAt'], '2025-01-02')
        self.assertFalse(any(call.args[1] in {'tags', 'summary', 'pinned', 'aiRefresh'} for call in labels.call_args_list))
        before = Path(sync.OUT_CONTENT_TS).read_bytes(), Path(sync.OUT_META_TS).read_bytes()
        self.execute()
        self.assertEqual(before, (Path(sync.OUT_CONTENT_TS).read_bytes(), Path(sync.OUT_META_TS).read_bytes()))

    def test_manual_summary_migration_uses_labels_and_published_cache_without_drafts(self):
        handwritten = '这里是作者自己填写的摘要。\n' + '保留完整文字。' * 20
        self.notes[1]['attributes'] += note('A', **{'摘要': handwritten})['attributes']
        result, report, labels, content = self.execute()
        self.assertEqual(result, 0)
        content.assert_not_called()
        article = next(item for item in sync.load_existing_content_records() if item['id'] == 'A')
        self.assertEqual(article['manualSummary'], handwritten)
        self.assertEqual(article['summary'], handwritten)
        self.assertEqual(article['contentHtml'], self.body)
        self.assertTrue(any(attr['name'] == 'label:摘要' for attr in self.notes[-1]['attributes']))
        self.notes[1]['attributes'] = [attr for attr in self.notes[1]['attributes'] if attr['name'] != '摘要']
        result, report, labels, content = self.execute()
        content.assert_not_called()
        article = next(item for item in sync.load_existing_content_records() if item['id'] == 'A')
        self.assertEqual(article['manualSummary'], '')
        self.assertEqual(article['summary'], '这是已经发布的正文，自动提取出简短摘要供小宠物展示。')

    def test_explicit_sync_includes_handwritten_summary_in_content_hash(self):
        item = note('A', publish='true', **{'摘要': '第一份手写摘要。'})
        with patch.object(sync, 'get_text', return_value=self.body), patch.object(sync, 'localize_attachments', side_effect=lambda text, note_id: (text, [], False)):
            first, _ = sync.build_post_record(item)
            item['attributes'][-1]['value'] = '第二份手写摘要。'
            second, _ = sync.build_post_record(item)
        self.assertNotEqual(first['syncHash'], second['syncHash'])
        self.assertEqual(second['manualSummary'], '第二份手写摘要。')
        self.assertEqual(second['summary'], '第二份手写摘要。')

    def test_targeted_sync_does_not_download_another_articles_draft(self):
        result, report, labels, content = self.execute('sync_requested', ['A'])
        self.assertEqual(result, 0)
        content.assert_called_once_with('/etapi/notes/A/content')
        self.assertEqual([item['id'] for item in report['updated']], ['A'])
        self.assertEqual([item['id'] for item in report['unchanged']], ['B'])
        self.assertEqual({item['id']: item['publishedAt'] for item in sync.load_existing_meta_records()}, {'A': '2025-01-01', 'B': '2025-01-01'})

    def test_url_identifier_migration_covers_unpublished_notes_without_fetching_bodies(self):
        self.notes.append(note('xtusIYhPQyK5', publish='false', slug='说说页面加速与自动更新原理详解'))
        result, report, labels, content = self.execute()
        self.assertEqual(result, 0)
        content.assert_not_called()
        labels.assert_any_call('xtusIYhPQyK5', 'slug', 'xtusIYhPQyK5')
        self.assertEqual(report['urlIdentifiersUpdated'], 3)
        _, repeated, _, _ = self.execute()
        self.assertEqual(repeated['urlIdentifiersUpdated'], 0)

    def test_title_or_custom_slug_cannot_change_an_articles_identifier(self):
        original = {'noteId': 'xtusIYhPQyK5', 'title': '说说页面加速与自动更新原理详解',
                    'attributes': note('xtusIYhPQyK5', slug='手工填写的标题')['attributes']}
        with patch.object(sync, 'get_text', return_value=self.body), patch.object(sync, 'localize_attachments', return_value=(self.body, [], False)):
            first, _ = sync.build_post_record(original)
            renamed, _ = sync.build_post_record({**original, 'title': '修改后的文章标题'})
            same_title, _ = sync.build_post_record({**original, 'noteId': 'OtherID'})
        self.assertEqual(first['slug'], 'xtusIYhPQyK5')
        self.assertEqual(renamed['slug'], first['slug'])
        self.assertEqual(same_title['slug'], 'OtherID')
        for invalid in ['', '../secret', '中文', 'a/b', 'a?x=1', None]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                note_url_identifier(invalid)

    def test_real_attribute_writer_preserves_capital_T_in_note_ids(self):
        identifier = '4o1kCT4CizAt'
        existing = note(identifier, slug='旧中文标题')
        with patch.object(sync, 'get_json', return_value=existing), patch.object(sync, 'patch_json') as update:
            sync.set_label(identifier, 'slug', identifier)
            update.assert_called_once_with('/etapi/attributes/' + identifier + 'slug', {'value': identifier})
        with patch.object(sync, 'get_json', return_value=note(identifier)), patch.object(sync, 'post_json') as create:
            sync.set_label(identifier, 'slug', identifier)
            self.assertEqual(create.call_args.args[1]['value'], identifier)
        with patch.object(sync, 'get_json', return_value=note(identifier)), patch.object(sync, 'post_json') as create:
            sync.set_label(identifier, 'publishedAt', '2025-01-01T12:00:00+0800')
            self.assertEqual(create.call_args.args[1]['value'], '2025-01-01 12:00:00+0800')

    def test_conversion_failure_keeps_generated_snapshot_and_assets(self):
        before = Path(sync.OUT_CONTENT_TS).read_bytes(), Path(sync.OUT_META_TS).read_bytes()
        with patch.object(sync, 'prune_unused_assets') as prune:
            result, report, labels, content = self.execute('sync_requested', ['A'], RuntimeError('conversion failed'))
            prune.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(report['failed'][0]['id'], 'A')
        self.assertEqual(before, (Path(sync.OUT_CONTENT_TS).read_bytes(), Path(sync.OUT_META_TS).read_bytes()))

    def test_unpublish_excludes_article_and_preserves_date_registry(self):
        self.notes[1] = note('A', publish='false', syncStatus='published')
        result, report, labels, content = self.execute('publish_changed', ['A'])
        self.assertEqual(result, 0)
        self.assertEqual([item['id'] for item in report['removed']], ['A'])
        self.assertEqual([item['id'] for item in sync.load_existing_content_records()], ['B'])
        self.assertEqual(sync.load_published_at_registry()['A'], '2025-01-01')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {'LONGBLOG_RUNTIME_DIR': self.temp.name}):
            spec = importlib.util.spec_from_file_location('runner_test_instance', Path(__file__).resolve().parents[1] / 'service' / 'runner.py')
            self.runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.runner)
        for handler in self.runner.LOG.handlers[:]:
            self.runner.LOG.removeHandler(handler)
            handler.close()
        self.runner.LOG = Mock()
        self.runner.EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.event = {'event': 'sync_requested', 'noteIds': ['A', 'B'], 'source': 'poller'}
        self.runner.EVENT_FILE.write_text(json.dumps(self.event))

    def test_passes_all_target_ids_without_retired_flags(self):
        event, args = self.runner.event_args()
        self.assertEqual(args, ['--event', 'sync_requested', '--noteIds', 'A', 'B'])

    def test_build_failure_never_commits_and_keeps_retry_context(self):
        run = Mock(side_effect=[Mock(stdout=json.dumps({'updated': [], 'failed': []})), Mock(stdout=' M content.ts')])
        with patch.object(self.runner, 'prepare_workspace'), patch.object(self.runner, 'run', run), \
             patch.object(self.runner, 'build', side_effect=RuntimeError('build failed')), \
             patch.object(self.runner, 'commit_and_push') as commit, patch.object(self.runner, 'notify_report'):
            self.assertEqual(self.runner.main(), 1)
        commit.assert_not_called()
        self.assertTrue(self.runner.EVENT_FILE.exists())
        self.assertFalse(json.loads(self.runner.REPORT_FILE.read_text())['gitPushed'])

    def test_conversion_failure_never_builds_or_pushes_partial_snapshot(self):
        with patch.object(self.runner, 'prepare_workspace'), \
             patch.object(self.runner, 'run', return_value=Mock(stdout=json.dumps({'failed': [{'id': 'A'}]}))), \
             patch.object(self.runner, 'build') as build, patch.object(self.runner, 'commit_and_push') as commit, \
             patch.object(self.runner, 'notify_report'):
            self.assertEqual(self.runner.main(), 1)
        build.assert_not_called()
        commit.assert_not_called()
        self.assertTrue(self.runner.EVENT_FILE.exists())


if __name__ == '__main__':
    unittest.main()
