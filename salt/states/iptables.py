# -*- coding: utf-8 -*-
'''
Management of iptables
======================

This is an iptables-specific module designed to manage Linux firewalls. It is
expected that this state module, and other system-specific firewall states, may
at some point be deprecated in favor of a more generic `firewall` state.

.. code-block:: yaml

    httpd:
      iptables.append:
        - table: filter
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.append:
        - table: filter
        - family: ipv6
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.insert:
        - position: 1
        - table: filter
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.insert:
        - position: 1
        - table: filter
        - family: ipv6
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.delete:
        - table: filter
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.delete:
        - position: 1
        - table: filter
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True

    httpd:
      iptables.delete:
        - table: filter
        - family: ipv6
        - chain: INPUT
        - jump: ACCEPT
        - match: state
        - connstate: NEW
        - dport: 80
        - proto: tcp
        - sport: 1025:65535
        - save: True


'''

# Import salt libs
from salt.state import STATE_INTERNAL_KEYWORDS as _STATE_INTERNAL_KEYWORDS


def __virtual__():
    '''
    Only load if the locale module is available in __salt__
    '''
    return 'iptables' if 'iptables.version' in __salt__ else False


def chain_present(name, table='filter', family='ipv4'):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Verify the chain is exist.

    name
        A user-defined chain name.

    table
        The table to own the chain.

    family
        Networking family, either ipv4 or ipv6
    '''

    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    chain_check = __salt__['iptables.check_chain'](table, name, family)
    if chain_check is True:
        ret['result'] = True
        ret['comment'] = ('iptables {0} chain is already exist in {1} table for {2}'
                          .format(name, table, family))
        return ret

    command = __salt__['iptables.new_chain'](table, name, family)
    if command is True:
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = ('iptables {0} chain in {1} table create success for {2}'
                          .format(name, table, family))
        return ret
    else:
        ret['result'] = False
        ret['comment'] = 'Failed to create {0} chain in {1} table: {2} for {3}'.format(
            name,
            table,
            command.strip(),
            family
        )
        return ret


def chain_absent(name, table='filter', family='ipv4'):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Verify the chain is absent.

    family
        Networking family, either ipv4 or ipv6
    '''

    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    chain_check = __salt__['iptables.check_chain'](table, name, family)
    if not chain_check:
        ret['result'] = True
        ret['comment'] = ('iptables {0} chain is already absent in {1} table for {2}'
                          .format(name, table, family))
        return ret

    flush_chain = __salt__['iptables.flush'](table, name, family)
    if not flush_chain:
        command = __salt__['iptables.delete_chain'](table, name, family)
        if command is True:
            ret['changes'] = {'locale': name}
            ret['result'] = True
            ret['comment'] = ('iptables {0} chain in {1} table delete success for {2}'
                              .format(name, table, family))
        else:
            ret['result'] = False
            ret['comment'] = ('Failed to delete {0} chain in {1} table: {2} for {2}'
                              .format(name, table, command.strip(), family))
    else:
        ret['result'] = False
        ret['comment'] = 'Failed to flush {0} chain in {1} table: {2} for {3}'.format(
            name,
            table,
            flush_chain.strip(),
            family
        )
    return ret


def append(name, family='ipv4', **kwargs):
    '''
    .. versionadded:: 0.17.0

    Append a rule to a chain

    name
        A user-defined name to call this rule by in another part of a state or
        formula. This should not be an actual rule.

    family
        Network family, ipv4 or ipv6.

    All other arguments are passed in with the same name as the long option
    that would normally be used for iptables, with one exception: `--state` is
    specified as `connstate` instead of `state` (not to be confused with
    `ctstate`).
    '''
    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    for ignore in _STATE_INTERNAL_KEYWORDS:
        if ignore in kwargs:
            del kwargs[ignore]
    rule = __salt__['iptables.build_rule'](family=family, **kwargs)
    command = __salt__['iptables.build_rule'](full=True, family=family, command='A', **kwargs)
    if __salt__['iptables.check'](kwargs['table'],
                                  kwargs['chain'],
                                  rule,
                                  family) is True:
        ret['result'] = True
        ret['comment'] = 'iptables rule for {0} already set ({1}) for {1}'.format(
            name,
            command.strip(),
            family)
        return ret
    if __opts__['test']:
        ret['comment'] = 'iptables rule for {0} needs to be set ({1}) for {2}'.format(
            name,
            command.strip(),
            family)
        return ret
    if __salt__['iptables.append'](kwargs['table'], kwargs['chain'], rule, family):
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = 'Set iptables rule for {0} to: {1} for {2}'.format(
            name,
            command.strip(),
            family)
        if 'save' in kwargs:
            if kwargs['save']:
                __salt__['iptables.save'](filename=None, family=family)
                ret['comment'] = ('Set and Saved iptables rule for {0} to: '
                                  '{1} for {2}'.format(name, command.strip(), family))
        return ret
    else:
        ret['result'] = False
        ret['comment'] = ('Failed to set iptables rule for {0}.\n'
                          'Attempted rule was {1} for {2}').format(
                              name,
                              command.strip(), family)
        return ret


def insert(name, family='ipv4', **kwargs):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Insert a rule into a chain

    name
        A user-defined name to call this rule by in another part of a state or
        formula. This should not be an actual rule.

    family
        Networking family, either ipv4 or ipv6

    All other arguments are passed in with the same name as the long option
    that would normally be used for iptables, with one exception: `--state` is
    specified as `connstate` instead of `state` (not to be confused with
    `ctstate`).
    '''
    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    for ignore in _STATE_INTERNAL_KEYWORDS:
        if ignore in kwargs:
            del kwargs[ignore]
    rule = __salt__['iptables.build_rule'](family=family, **kwargs)
    command = __salt__['iptables.build_rule'](full=True, family=family, command='I', **kwargs)
    if __salt__['iptables.check'](kwargs['table'],
                                  kwargs['chain'],
                                  rule,
                                  family) is True:
        ret['result'] = True
        ret['comment'] = 'iptables rule for {0} already set for {1} ({2})'.format(
            name,
            family,
            command.strip())
        return ret
    if __opts__['test']:
        ret['comment'] = 'iptables rule for {0} needs to be set for {1} ({2})'.format(
            name,
            family,
            command.strip())
        return ret
    if not __salt__['iptables.insert'](kwargs['table'], kwargs['chain'], kwargs['position'], rule, family):
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = 'Set iptables rule for {0} to: {1} for {2}'.format(
            name,
            command.strip(),
            family)
        if 'save' in kwargs:
            if kwargs['save']:
                __salt__['iptables.save'](filename=None, family=family)
                ret['comment'] = ('Set and Saved iptables rule for {0} to: '
                                  '{1} for {2}'.format(name, command.strip(), family))
        return ret
    else:
        ret['result'] = False
        ret['comment'] = ('Failed to set iptables rule for {0}.\n'
                          'Attempted rule was {1}').format(
                              name,
                              command.strip())
        return ret


def delete(name, family='ipv4', **kwargs):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Delete a rule to a chain

    name
        A user-defined name to call this rule by in another part of a state or
        formula. This should not be an actual rule.

    family
        Networking family, either ipv4 or ipv6

    All other arguments are passed in with the same name as the long option
    that would normally be used for iptables, with one exception: `--state` is
    specified as `connstate` instead of `state` (not to be confused with
    `ctstate`).
    '''
    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    for ignore in _STATE_INTERNAL_KEYWORDS:
        if ignore in kwargs:
            del kwargs[ignore]
    rule = __salt__['iptables.build_rule'](family=family, **kwargs)
    command = __salt__['iptables.build_rule'](full=True, family=family, command='D', **kwargs)
    if not __salt__['iptables.check'](kwargs['table'],
                                  kwargs['chain'],
                                  rule,
                                  family) is True:
        ret['result'] = True
        ret['comment'] = 'iptables rule for {0} already absent for {1} ({2})'.format(
            name,
            family,
            command.strip())
        return ret
    if __opts__['test']:
        ret['comment'] = 'iptables rule for {0} needs to be deleted for {1} ({2})'.format(
            name,
            family,
            command.strip())
        return ret

    if 'position' in kwargs:
        result = __salt__['iptables.delete'](
                kwargs['table'],
                kwargs['chain'],
                family=family,
                position=kwargs['position'])
    else:
        result = __salt__['iptables.delete'](
                kwargs['table'],
                kwargs['chain'],
                family=family,
                rule=rule)

    if not result:
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = 'Delete iptables rule for {1} {1}'.format(
            name,
            command.strip())
        if 'save' in kwargs:
            if kwargs['save']:
                __salt__['iptables.save'](filename=None, family=family)
                ret['comment'] = ('Deleted and Saved iptables rule for {0} for {1}'
                                  '{2}'.format(name, command.strip()), family)
        return ret
    else:
        ret['result'] = False
        ret['comment'] = ('Failed to delete iptables rule for {0}.\n'
                          'Attempted rule was {1}').format(
                              name,
                              command.strip())
        return ret


def set_policy(name, family='ipv4', **kwargs):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Sets the default policy for iptables firewall tables

    family
        Networking family, either ipv4 or ipv6

    '''
    ret = {'name': name,
        'changes': {},
        'result': None,
        'comment': ''}

    for ignore in _STATE_INTERNAL_KEYWORDS:
        if ignore in kwargs:
            del kwargs[ignore]

    if __salt__['iptables.get_policy'](
            kwargs['table'],
            kwargs['chain'],
            family) == kwargs['policy']:
        ret['result'] = True
        ret['comment'] = ('iptables default policy for {0} for {1} already set to {2}'
                          .format(kwargs['table'], family, kwargs['policy']))
        return ret

    if not __salt__['iptables.set_policy'](
            kwargs['table'],
            kwargs['chain'],
            kwargs['policy'],
            family):
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = 'Set default policy for {0} to {1} family {2}'.format(
            kwargs['chain'],
            kwargs['policy'],
            family
        )
        return ret
    else:
        ret['result'] = False
        ret['comment'] = 'Failed to set iptables default policy'
        return ret


def flush(name, family='ipv4', **kwargs):
    '''
    .. versionadded:: 2014.1.0 (Hydrogen)

    Flush current iptables state

    family
        Networking family, either ipv4 or ipv6

    '''
    ret = {'name': name,
           'changes': {},
           'result': None,
           'comment': ''}

    for ignore in _STATE_INTERNAL_KEYWORDS:
        if ignore in kwargs:
            del kwargs[ignore]

    if not 'chain' in kwargs:
        kwargs['chain'] = ''

    if not __salt__['iptables.flush'](kwargs['table'], kwargs['chain'], family):
        ret['changes'] = {'locale': name}
        ret['result'] = True
        ret['comment'] = 'Flush iptables rules in {0} table {1} chain {2} family'.format(
            kwargs['table'],
            kwargs['chain'],
            family
        )
        return ret
    else:
        ret['result'] = False
        ret['comment'] = 'Failed to flush iptables rules'
        return ret
