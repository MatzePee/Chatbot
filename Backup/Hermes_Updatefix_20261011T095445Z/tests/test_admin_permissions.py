"""Verbose-list fixtures match sudo and sudo-rs; never run an administrative action.

sudo-rs documents this output in its upstream long_format snapshots:
https://github.com/trifectatechfoundation/sudo-rs/tree/main/test-framework/sudo-compliance-tests/src/sudo/flag_list/long_format
"""
import subprocess
import pytest
from deploy import admin_permissions as admin


def entry(commands, options='!authenticate', target='root', group=''):
    return ('Sudoers entry:\n    RunAsUsers: ' + target + '\n'
            + ('    RunAsGroups: ' + group + '\n' if group else '')
            + ('    Options: ' + options + '\n' if options else '')
            + '    Commands:\n' + ''.join('\t' + command + '\n' for command in commands) + '\n')


@pytest.mark.parametrize('action', admin.ACTIONS)
def test_normal_user_all_rule_then_scoped_passwordless_rule(action):
    listing = entry(['ALL'], options='', target='ALL', group='ALL')
    listing += entry([admin.HELPER + ' ' + a for a in admin.ACTIONS])
    assert admin.passwordless_listing(listing, action)


@pytest.mark.parametrize('override', [
    entry(['ALL'], options='authenticate'),
    entry([admin.HELPER + ' update'], options='authenticate'),
    entry(['!' + admin.HELPER + ' update']),
    entry([admin.HELPER + ' up*'], options='authenticate'),
    entry(['/usr/local/bin/* update'], options='authenticate'),
    entry([admin.HELPER], options='authenticate'),
    entry(['UNRESOLVED_ALIAS']),
    entry([admin.HELPER + ' ^up.*$']),
])
def test_later_conflicting_or_unresolved_rules_cannot_pass(override):
    assert not admin.passwordless_listing(entry([admin.HELPER + ' update']) + override, 'update')


def test_passwordless_rule_for_other_command_cannot_authorize_update():
    assert not admin.passwordless_listing(entry(['/usr/bin/true']) + entry([admin.HELPER + ' update'], options=''), 'update')


@pytest.mark.parametrize('target,group', [('!root', ''), ('daemon', ''), ('root', 'daemon'), ('UNKNOWN_ALIAS', '')])
def test_non_root_or_unresolved_target_cannot_grant_access(target, group):
    assert not admin.passwordless_listing(entry([admin.HELPER + ' update'], target=target, group=group), 'update')


def test_plain_command_output_is_not_a_passwordless_confirmation():
    assert not admin.passwordless_listing(admin.HELPER + ' update\n', 'update')


def test_no_passwordless_rule_defaults_to_rejected():
    assert not admin.passwordless_listing(entry([admin.HELPER + ' update'], options=''), 'update')


def test_command_denied_by_sudo_is_rejected_even_with_listing(monkeypatch):
    def result(args, **kwargs):
        if args[-1] == '-ll':
            return subprocess.CompletedProcess(args, 0, stdout=entry([admin.HELPER]))
        return subprocess.CompletedProcess(args, int(args[-1] == 'update'), stdout=admin.HELPER + ' ' + args[-1])
    monkeypatch.setattr(admin.subprocess, 'run', result)
    assert admin.check() == ['update']


def test_failed_listing_cannot_succeed_from_cached_credentials(monkeypatch):
    monkeypatch.setattr(admin.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout=''))
    assert admin.check() == list(admin.ACTIONS)
