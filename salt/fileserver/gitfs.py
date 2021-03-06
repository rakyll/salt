# -*- coding: utf-8 -*-
'''
Git Fileserver Backend

With this backend, branches and tags in a remote git repository are exposed to
salt as different environments.

To enable, add ``git`` to the :conf_master:`fileserver_backend` option in the
master config file.

As of the :strong:`Helium` release, the Git fileserver backend will support
`GitPython`_, `pygit2`_, and `dulwich`_ to provide the Python interface to git.
If more than one of these are present, the order of preference for which one
will be chosen is the same as the order in which they were listed: GitPython,
pygit2, dulwich (keep in mind, this order is subject to change).

**pygit2 and dulwich support presently exist only in the develop branch and are
not yet available in an official release**

An optional master config parameter (:conf_master:`gitfs_provider`) can be used
to specify which provider should be used.

.. note:: Minimum requirements

    Using `GitPython`_ requires a minimum GitPython version of 0.3.0, as well as
    git itself.

    Using `pygit2`_ requires a minimum pygit2 version of 0.19.0. Additionally,
    using pygit2 as a provider requires `libgit2`_ 0.19.0 or newer, as well as
    git itself. pygit2 and libgit2 are developed alongside one another, so it
    is recommended to keep them both at the same major release to avoid
    unexpected behavior.

.. warning::

    `pygit2`_ does not yet support supplying passing SSH credentials, so at
    this time only ``http://``, ``https://``, and ``file://`` URIs are
    supported as valid :conf_master:`gitfs_remotes` entries if pygit2 is being
    used.

    Additionally, `pygit2`_ does not yet support passing http/https credentials
    via a `.netrc`_ file.

.. _GitPython: https://github.com/gitpython-developers/GitPython
.. _pygit2: https://github.com/libgit2/pygit2
.. _libgit2: https://github.com/libgit2/pygit2#quick-install-guide
.. _dulwich: https://www.samba.org/~jelmer/dulwich/
.. _.netrc: https://www.gnu.org/software/inetutils/manual/html_node/The-_002enetrc-File.html
'''

# Import python libs
import distutils.version  # pylint: disable=E0611
import glob
import hashlib
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime

VALID_PROVIDERS = ('gitpython', 'pygit2', 'dulwich')
PYGIT2_TRANSPORTS = ('http', 'https', 'file')
PER_REMOTE_PARAMS = ('mountpoint', 'root')

_RECOMMEND_GITPYTHON = (
    'GitPython is installed, you may wish to set gitfs_provider to '
    '\'gitpython\' in the master config file to use GitPython for gitfs '
    'support.'
)

_RECOMMEND_PYGIT2 = (
    'pygit2 is installed, you may wish to set gitfs_provider to '
    '\'pygit2\' in the master config file to use pygit2 for for gitfs '
    'support.'
)

_RECOMMEND_DULWICH = (
    'Dulwich is installed, you may wish to set gitfs_provider to '
    '\'dulwich\' in the master config file to use Dulwich for gitfs '
    'support.'
)

_INVALID_REPO = (
    'Cache path {0} (corresponding remote: {1}) exists but is not a valid '
    'git repository. You will need to manually delete this directory on the '
    'master to continue to use this gitfs remote.'
)

# Import salt libs
import salt.utils
import salt.fileserver
from salt.exceptions import SaltException
from salt.utils.event import tagify

# Import third party libs
try:
    import git
    import gitdb
    HAS_GITPYTHON = True
except ImportError:
    HAS_GITPYTHON = False

try:
    import pygit2
    HAS_PYGIT2 = True
except ImportError:
    HAS_PYGIT2 = False

try:
    import dulwich.repo
    import dulwich.client
    import dulwich.config
    import dulwich.objects
    HAS_DULWICH = True
except ImportError:
    HAS_DULWICH = False

log = logging.getLogger(__name__)

# Define the module's virtual name
__virtualname__ = 'git'


def _verify_gitpython(quiet=False):
    '''
    Check if GitPython is available and at a compatible version (>= 0.3.0)
    '''
    if not HAS_GITPYTHON:
        log.error(
            'Git fileserver backend is enabled in master config file, but '
            'could not be loaded, is GitPython installed?'
        )
        if HAS_PYGIT2 and not quiet:
            log.error(_RECOMMEND_PYGIT2)
        if HAS_DULWICH and not quiet:
            log.error(_RECOMMEND_DULWICH)
        return False
    gitver = distutils.version.LooseVersion(git.__version__)
    minver_str = '0.3.0'
    minver = distutils.version.LooseVersion(minver_str)
    errors = []
    if gitver < minver:
        errors.append(
            'Git fileserver backend is enabled in master config file, but '
            'the GitPython version is earlier than {0}. Version {1} '
            'detected.'.format(minver_str, git.__version__)
        )
    if errors:
        if HAS_PYGIT2 and not quiet:
            errors.append(_RECOMMEND_PYGIT2)
        if HAS_DULWICH and not quiet:
            errors.append(_RECOMMEND_DULWICH)
        for error in errors:
            log.error(error)
        return False
    log.info('gitpython gitfs_provider enabled')
    __opts__['verified_gitfs_provider'] = 'gitpython'
    return True


def _verify_pygit2(quiet=False):
    '''
    Check if pygit2/libgit2 are available and at a compatible version. Both
    must be at least 0.19.0.
    '''
    if not HAS_PYGIT2:
        log.error(
            'Git fileserver backend is enabled in master config file, but '
            'could not be loaded, are pygit2 and libgit2 installed?'
        )
        if HAS_GITPYTHON and not quiet:
            log.error(_RECOMMEND_GITPYTHON)
        if HAS_DULWICH and not quiet:
            log.error(_RECOMMEND_DULWICH)
        return False
    pygit2ver = distutils.version.LooseVersion(pygit2.__version__)
    libgit2ver = distutils.version.LooseVersion(pygit2.LIBGIT2_VERSION)
    minver_str = '0.19.0'
    minver = distutils.version.LooseVersion(minver_str)
    errors = []
    if pygit2ver < minver:
        errors.append(
            'Git fileserver backend is enabled in master config file, but '
            'pygit2 version is earlier than {0}. Version {1} detected.'
            .format(minver_str, pygit2.__version__)
        )
    if libgit2ver < minver:
        errors.append(
            'Git fileserver backend is enabled in master config file, but '
            'libgit2 version is earlier than {0}. Version {1} detected.'
            .format(minver_str, pygit2.__version__)
        )
    if not salt.utils.which('git'):
        errors.append(
            'The git command line utility is required by the Git fileserver '
            'backend when using the \'pygit2\' provider.'
        )
    if errors:
        if HAS_GITPYTHON and not quiet:
            errors.append(_RECOMMEND_GITPYTHON)
        if HAS_DULWICH and not quiet:
            errors.append(_RECOMMEND_DULWICH)
        for error in errors:
            log.error(error)
        return False
    log.info('pygit2 gitfs_provider enabled')
    __opts__['verified_gitfs_provider'] = 'pygit2'
    return True


def _verify_dulwich(quiet=False):
    '''
    Check if dulwich is available.
    '''
    if not HAS_DULWICH:
        log.error(
            'Git fileserver backend is enabled in master config file, but '
            'could not be loaded, is Dulwich installed?'
        )
        if HAS_GITPYTHON and not quiet:
            log.error(_RECOMMEND_GITPYTHON)
        if HAS_PYGIT2 and not quiet:
            log.error(_RECOMMEND_PYGIT2)
        return False
    log.info('dulwich gitfs_provider enabled')
    __opts__['verified_gitfs_provider'] = 'dulwich'
    return True


def _get_provider():
    '''
    Determin which gitfs_provider to use
    '''
    # Don't re-perform all the verification if we already have a verified
    # provider
    if 'verified_gitfs_provider' in __opts__:
        return __opts__['verified_gitfs_provider']
    provider = __opts__.get('gitfs_provider', '').lower()
    if not provider:
        # Prefer GitPython if it's available and verified
        if _verify_gitpython(quiet=True):
            return 'gitpython'
        elif _verify_pygit2(quiet=True):
            return 'pygit2'
        elif _verify_dulwich(quiet=True):
            return 'dulwich'
        else:
            log.error(
                'No suitable version of GitPython, pygit2/libgit2, or Dulwich '
                'is installed.'
            )
    else:
        if provider not in VALID_PROVIDERS:
            raise SaltException(
                'Invalid gitfs_provider {0!r}. Valid choices are: {1}'
                .format(provider, ', '.join(VALID_PROVIDERS))
            )
        elif provider == 'gitpython' and _verify_gitpython():
            return 'gitpython'
        elif provider == 'pygit2' and _verify_pygit2():
            return 'pygit2'
        elif provider == 'dulwich' and _verify_dulwich():
            return 'dulwich'
    return ''


def __virtual__():
    '''
    Only load if the desired provider module is present and gitfs is enabled
    properly in the master config file.
    '''
    if not __virtualname__ in __opts__['fileserver_backend']:
        return False
    try:
        return __virtualname__ if _get_provider() else False
    except SaltException as exc:
        log.error(exc)
        return False


def _dulwich_conf(repo):
    '''
    Returns a dulwich.config.ConfigFile object for the specified repo
    '''
    return dulwich.config.ConfigFile().from_path(
        os.path.join(repo.controldir(), 'config')
    )


def _dulwich_remote(repo):
    '''
    Returns the remote url for the specified repo
    '''
    return _dulwich_conf(repo).get(('remote', 'origin'), 'url')


def _dulwich_walk_tree(repo, tree, path):
    '''
    Dulwich does not provide a means of directly accessing subdirectories. This
    function will walk down to the directory specified by 'path', and return a
    Tree object at that path. If path is an empty string, the original tree
    will be returned, and if there are any issues encountered walking the tree,
    None will be returned.
    '''
    if not path:
        return tree
    # Walk down the tree to get to the file
    for parent in path.split(os.path.sep):
        try:
            tree = repo.get_object(tree[parent][1])
        except (KeyError, TypeError):
            # Directory not found, or tree passed into function is not a Tree
            # object. Either way, desired path does not exist.
            return None
    return tree


_dulwich_env_refs = lambda refs: [x for x in refs
                                  if re.match('refs/(heads|tags)', x)
                                  and not x.endswith('^{}')]


def _get_tree_gitpython(repo, short):
    '''
    Return a git.Tree object if the branch/tag/SHA is found, otherwise None
    '''
    if short in envs():
        for ref in repo.refs:
            if isinstance(ref, (git.RemoteReference, git.TagReference)):
                parted = ref.name.partition('/')
                rspec = parted[2] if parted[2] else parted[0]
                rspec = rspec.replace('/', '_')
                if rspec == short:
                    return ref.commit.tree

    # Branch or tag not matched, check if 'short' is a commit
    if not _env_is_exposed(short):
        return None
    try:
        commit = repo.rev_parse(short)
    except gitdb.exc.BadObject:
        pass
    else:
        return commit.tree
    return None


def _get_tree_pygit2(repo, short):
    '''
    Return a pygit2.Tree object if the branch/tag/SHA is found, otherwise None
    '''
    if short in envs():
        for ref in repo.listall_references():
            _, rtype, rspec = ref.split('/', 2)
            if rtype in ('remotes', 'tags'):
                parted = rspec.partition('/')
                rspec = parted[2] if parted[2] else parted[0]
                rspec = rspec.replace('/', '_')
                if rspec == short and _env_is_exposed(rspec):
                    return repo.lookup_reference(ref).get_object().tree

    # Branch or tag not matched, check if 'short' is a commit
    if not _env_is_exposed(short):
        return None
    try:
        commit = repo.revparse_single(short)
    except (KeyError, TypeError):
        # Not a valid commit, likely not a commit SHA
        pass
    else:
        return commit.tree
    return None


def _get_tree_dulwich(repo, short):
    '''
    Return a dulwich.objects.Tree object if the branch/tag/SHA is found,
    otherwise None
    '''
    if short in envs():
        refs = repo.get_refs()
        # Sorting ensures we check heads (branches) before tags
        for ref in sorted(_dulwich_env_refs(refs)):
            # ref will be something like 'refs/heads/master'
            rtype, rspec = ref[5:].split('/', 1)
            rspec = rspec.replace('/', '_')
            if rspec == short and _env_is_exposed(rspec):
                if rtype == 'heads':
                    commit = repo.get_object(refs[ref])
                elif rtype == 'tags':
                    tag = repo.get_object(refs[ref])
                    if isinstance(tag, dulwich.objects.Tag):
                        # Tag.get_object() returns a 2-tuple, the 2nd element
                        # of which is the commit SHA to which the tag refers
                        commit = repo.get_object(tag.object[1])
                    elif isinstance(tag, dulwich.objects.Commit):
                        commit = tag
                    else:
                        log.error(
                            'Unhandled object type {0!r} in '
                            '_get_tree_dulwich. This is a bug, please report '
                            'it.'.format(tag.type_name)
                        )
                return repo.get_object(commit.tree)

    # Branch or tag not matched, check if 'short' is a commit. This is more
    # difficult with Dulwich because of its inability to deal with shortened
    # SHA-1 hashes.
    if not _env_is_exposed(short):
        return None
    try:
        int(short, 16)
    except ValueError:
        # Not hexidecimal, likely just a non-matching environment
        return None

    try:
        if len(short) == 40:
            sha_obj = repo.get_object(short)
            if isinstance(sha_obj, dulwich.objects.Commit):
                sha_commit = sha_obj
        else:
            matches = set([
                x for x in (
                    repo.get_object(x) for x in repo.object_store
                    if x.startswith(short)
                )
                if isinstance(x, dulwich.objects.Commit)
            ])
            if len(matches) > 1:
                log.warning('Ambiguous commit ID {0!r}'.format(short))
                return None
            try:
                sha_commit = matches.pop()
            except IndexError:
                pass
    except TypeError as exc:
        log.warning('Invalid environment {0}: {1}'.format(short, exc))
    except KeyError:
        # No matching SHA
        return None

    try:
        return repo.get_object(sha_commit.tree)
    except NameError:
        # No matching sha_commit object was created. Unable to find SHA.
        pass
    return None


def _stale_refs_pygit2(repo):
    '''
    Return a list of stale refs by running git remote prune --dry-run <remote>,
    since pygit2 can't do this.
    '''
    key = ' * [would prune] '
    ret = []
    for line in subprocess.Popen(
            'git remote prune --dry-run origin',
            shell=True,
            close_fds=True,
            cwd=repo.workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT).communicate()[0].splitlines():
        if line.startswith(key):
            line = line.replace(key, '')
            ret.append(line)
    return ret


def init():
    '''
    Return the git repo object for this session
    '''
    bp_ = os.path.join(__opts__['cachedir'], 'gitfs')
    provider = _get_provider()
    # ignore git ssl verification if requested
    ssl_verify = 'true' if __opts__.get('gitfs_ssl_verify', True) else 'false'
    new_remote = False
    repos = []
    gitfs_remotes = salt.utils.repack_dictlist(__opts__['gitfs_remotes'])
    for repo_uri, repo_conf_params in gitfs_remotes.iteritems():

        # Check repo_uri against the list of valid protocols
        if provider == 'pygit2':
            transport, _, uri = repo_uri.partition('://')
            if not uri:
                log.error('Invalid gitfs remote {0!r}'.format(repo_uri))
                continue
            elif transport.lower() not in PYGIT2_TRANSPORTS:
                log.error(
                    'Invalid transport {0!r} in gitfs remote {1!r}. Valid '
                    'transports for pygit2 provider: {2}'
                    .format(transport, repo_uri, ', '.join(PYGIT2_TRANSPORTS))
                )
                continue

        # Validate and compile per-remote configuration parameters, if present
        repo_conf = dict([(x, None) for x in PER_REMOTE_PARAMS])
        if repo_conf_params is not None:
            repo_conf_params = salt.utils.repack_dictlist(repo_conf_params)
            if not repo_conf_params:
                log.error(
                    'Invalid per-remote configuration for remote {0!r}'
                    .format(repo_uri)
                )
            else:
                for param, value in repo_conf_params.iteritems():
                    if param in PER_REMOTE_PARAMS:
                        repo_conf[param] = value
                    else:
                        log.error(
                            'Invalid configuration parameter {0!r} in remote '
                            '{1!r}. Valid parameters are: {2}. See the '
                            'documentation for further information.'
                            .format(
                                param, repo_uri, ', '.join(PER_REMOTE_PARAMS)
                            )
                        )
        try:
            repo_conf['mountpoint'] = salt.utils.strip_proto(
                repo_conf['mountpoint']
            )
        except TypeError:
            # mountpoint not specified
            pass

        repo_hash = hashlib.md5(repo_uri).hexdigest()
        rp_ = os.path.join(bp_, repo_hash)
        if not os.path.isdir(rp_):
            os.makedirs(rp_)

        try:
            if provider == 'gitpython':
                repo, new = _init_gitpython(rp_, repo_uri, ssl_verify)
                if new:
                    new_remote = True
            elif provider == 'pygit2':
                repo, new = _init_pygit2(rp_, repo_uri, ssl_verify)
                if new:
                    new_remote = True
            elif provider == 'dulwich':
                repo, new = _init_dulwich(rp_, repo_uri, ssl_verify)
                if new:
                    new_remote = True
            else:
                # Should never get here because the provider has been verified
                # in __virtual__(). Log an error and return an empty list.
                log.error(
                    'Unexpected gitfs_provider {0!r}. This is probably a bug.'
                    .format(provider)
                )
                return []

            if repo is not None:
                repo_conf.update({
                    'repo': repo,
                    'uri': repo_uri,
                    'hash': repo_hash,
                    'cachedir': rp_
                })
                repos.append(repo_conf)

        except Exception as exc:
            msg = ('Exception caught while initializing the repo for gitfs: '
                   '{0}.'.format(exc))
            if provider == 'gitpython':
                msg += ' Perhaps git is not available.'
            log.error(msg)
            continue

    if new_remote:
        remote_map = os.path.join(__opts__['cachedir'], 'gitfs/remote_map.txt')
        try:
            with salt.utils.fopen(remote_map, 'w+') as fp_:
                timestamp = datetime.now().strftime('%d %b %Y %H:%M:%S.%f')
                fp_.write('# gitfs_remote map as of {0}\n'.format(timestamp))
                for repo_conf in repos:
                    fp_.write(
                        '{0} = {1}\n'.format(
                            repo_conf['hash'], repo_conf['uri']
                        )
                    )
        except OSError:
            pass
        else:
            log.info('Wrote new gitfs_remote map to {0}'.format(remote_map))

    return repos


def _init_gitpython(rp_, repo_uri, ssl_verify):
    '''
    Initialize/attach to a repository using GitPython. Return the repo object
    if successful, otherwise return None. Also return a boolean that will tell
    init() whether a new repo was initialized.
    '''
    new = False
    if not os.listdir(rp_):
        # Repo cachedir is empty, initialize a new repo there
        repo = git.Repo.init(rp_)
        new = True
    else:
        # Repo cachedir exists, try to attach
        try:
            repo = git.Repo(rp_)
        except git.exc.InvalidGitRepositoryError:
            log.error(_INVALID_REPO.format(rp_, repo_uri))
            return None, new
    if not repo.remotes:
        try:
            repo.create_remote('origin', repo_uri)
            repo.git.config('http.sslVerify', ssl_verify)
        except os.error:
            # This exception occurs when two processes are trying to write to
            # the git config at once, go ahead and pass over it since this is
            # the only write. This should place a lock down.
            pass
    if repo.remotes:
        return repo, new
    return None, new


def _init_pygit2(rp_, repo_uri, ssl_verify):
    '''
    Initialize/attach to a repository using pygit2. Return the repo object if
    successful, otherwise return None. Also return a boolean that will tell
    init() whether a new repo was initialized.
    '''
    new = False
    if not os.listdir(rp_):
        # Repo cachedir is empty, initialize a new repo there
        repo = pygit2.init_repository(rp_)
        new = True
    else:
        # Repo cachedir exists, try to attach
        try:
            repo = pygit2.Repository(rp_)
        except KeyError:
            log.error(_INVALID_REPO.format(rp_, repo_uri))
            return None, new
    if not repo.remotes:
        try:
            repo.create_remote('origin', repo_uri)
            repo.config.set_multivar('http.sslVerify', '', ssl_verify)
        except os.error:
            # This exception occurs when two processes are trying to write to
            # the git config at once, go ahead and pass over it since this is
            # the only write. This should place a lock down.
            pass
    if repo.remotes:
        return repo, new
    return None, new


def _init_dulwich(rp_, repo_uri, ssl_verify):
    '''
    Initialize/attach to a repository using Dulwich. Return the repo object if
    successful, otherwise return None. Also return a boolean that will tell
    init() whether a new repo was initialized.
    '''
    new = False
    if not os.listdir(rp_):
        # Repo cachedir is empty, initialize a new repo there
        try:
            repo = dulwich.repo.Repo.init(rp_)
            new = True
            conf = _dulwich_conf(repo)
            conf.set('http', 'sslVerify', ssl_verify)
            # Add the remote manually, there is no function/object to do this
            conf.set(
                'remote "origin"',
                'fetch',
                '+refs/heads/*:refs/remotes/origin/*'
            )
            conf.set('remote "origin"', 'url', repo_uri)
            conf.set('remote "origin"', 'pushurl', repo_uri)
            conf.write_to_path()
        except os.error:
            pass
    else:
        # Repo cachedir exists, try to attach
        try:
            repo = dulwich.repo.Repo(rp_)
        except dulwich.repo.NotGitRepository:
            log.error(_INVALID_REPO.format(rp_, repo_uri))
            return None, new
    # No way to interact with remotes, so just assume success
    return repo, new


def purge_cache():
    bp_ = os.path.join(__opts__['cachedir'], 'gitfs')
    try:
        remove_dirs = os.listdir(bp_)
    except OSError:
        remove_dirs = []
    for repo_conf in init():
        try:
            remove_dirs.remove(repo_conf['hash'])
        except ValueError:
            pass
    remove_dirs = [os.path.join(bp_, rdir) for rdir in remove_dirs
                   if rdir not in ('hash', 'refs', 'envs.p', 'remote_map.txt')]
    if remove_dirs:
        for rdir in remove_dirs:
            shutil.rmtree(rdir)
        return True
    return False


def update():
    '''
    Execute a git fetch on all of the repos
    '''
    # data for the fileserver event
    data = {'changed': False,
            'backend': 'gitfs'}
    provider = _get_provider()
    pid = os.getpid()
    data['changed'] = purge_cache()
    for repo_conf in init():
        repo = repo_conf['repo']
        if provider == 'gitpython':
            origin = repo.remotes[0]
            working_dir = repo.working_dir
        elif provider == 'pygit2':
            origin = repo.remotes[0]
            working_dir = repo.workdir
        elif provider == 'dulwich':
            # origin is just a uri here, there is no origin object
            origin = repo_conf['uri']
            working_dir = repo.path
        lk_fn = os.path.join(working_dir, 'update.lk')
        with salt.utils.fopen(lk_fn, 'w+') as fp_:
            fp_.write(str(pid))
        try:
            if provider == 'gitpython':
                for fetch in origin.fetch():
                    if fetch.old_commit is not None:
                        data['changed'] = True
            elif provider == 'pygit2':
                fetch = origin.fetch()
                if fetch.get('received_objects', 0):
                    data['changed'] = True
            elif provider == 'dulwich':
                client, path = \
                    dulwich.client.get_transport_and_path_from_url(
                        origin, thin_packs=True
                    )
                refs_pre = repo.get_refs()
                try:
                    refs_post = client.fetch(path, repo)
                except KeyError:
                    log.critical(
                        'Local repository cachedir {0!r} (corresponding '
                        'remote: {1}) has been corrupted. Salt will now '
                        'attempt to remove the local checkout to allow it to '
                        'be re-initialized in the next fileserver cache '
                        'update.'
                        .format(repo_conf['cachedir'], repo_conf['uri'])
                    )
                    try:
                        salt.utils.rm_rf(repo_conf['cachedir'])
                    except OSError as exc:
                        log.critical(
                            'Unable to remove {0!r}: {1}'
                            .format(repo_conf['cachedir'], exc)
                        )
                    continue
                if refs_post is None:
                    # Empty repository
                    log.warning(
                        'gitfs remote {0!r} is an empty repository and will '
                        'be skipped.'.format(origin)
                    )
                    continue
                if refs_pre != refs_post:
                    data['changed'] = True
                    # Update local refs
                    for ref in _dulwich_env_refs(refs_post):
                        repo[ref] = refs_post[ref]
                    # Prune stale refs
                    for ref in repo.get_refs():
                        if ref not in refs_post:
                            del repo[ref]
        except Exception as exc:
            log.warning(
                'Exception caught while fetching: {0}'.format(exc)
            )
        try:
            os.remove(lk_fn)
        except (IOError, OSError):
            pass

    env_cache = os.path.join(__opts__['cachedir'], 'gitfs/envs.p')
    if data.get('changed', False) is True or not os.path.isfile(env_cache):
        new_envs = envs(ignore_cache=True)
        serial = salt.payload.Serial(__opts__)
        with salt.utils.fopen(env_cache, 'w+') as fp_:
            fp_.write(serial.dumps(new_envs))
            log.trace('Wrote env cache data to {0}'.format(env_cache))

    # if there is a change, fire an event
    if __opts__.get('fileserver_events', False):
        event = salt.utils.event.MasterEvent(__opts__['sock_dir'])
        event.fire_event(data, tagify(['gitfs', 'update'], prefix='fileserver'))
    try:
        salt.fileserver.reap_fileserver_cache_dir(
            os.path.join(__opts__['cachedir'], 'gitfs/hash'),
            find_file
        )
    except (IOError, OSError):
        # Hash file won't exist if no files have yet been served up
        pass


def _env_is_exposed(env):
    '''
    Check if an environment is exposed by comparing it against a whitelist and
    blacklist.
    '''
    return salt.utils.check_whitelist_blacklist(
        env,
        whitelist=__opts__['gitfs_env_whitelist'],
        blacklist=__opts__['gitfs_env_blacklist']
    )


def envs(ignore_cache=False):
    '''
    Return a list of refs that can be used as environments
    '''
    if not ignore_cache:
        env_cache = os.path.join(__opts__['cachedir'], 'gitfs/envs.p')
        cache_match = salt.fileserver.check_env_cache(__opts__, env_cache)
        if cache_match is not None:
            return cache_match
    base_branch = __opts__['gitfs_base']
    provider = _get_provider()
    ret = set()
    for repo_conf in init():
        if provider == 'gitpython':
            ret.update(_envs_gitpython(repo_conf['repo'], base_branch))
        elif provider == 'pygit2':
            ret.update(_envs_pygit2(repo_conf['repo'], base_branch))
        elif provider == 'dulwich':
            ret.update(_envs_dulwich(repo_conf['repo'], base_branch))
        else:
            # Should never get here because the provider has been verified
            # in __virtual__(). Log an error and return an empty list.
            log.error(
                'Unexpected gitfs_provider {0!r}. This is probably a bug.'
                .format(provider)
            )
            return []
    return sorted(ret)


def _envs_gitpython(repo, base_branch):
    '''
    Check the refs and return a list of the ones which can be used as salt
    environments.
    '''
    ret = set()
    remote = repo.remotes[0]
    for ref in repo.refs:
        parted = ref.name.partition('/')
        rspec = parted[2] if parted[2] else parted[0]
        rspec = rspec.replace('/', '_')
        if isinstance(ref, git.Head):
            if rspec == base_branch:
                rspec = 'base'
            if ref not in remote.stale_refs and _env_is_exposed(rspec):
                ret.add(rspec)
        elif isinstance(ref, git.Tag) and _env_is_exposed(rspec):
            ret.add(rspec)
    return ret


def _envs_pygit2(repo, base_branch):
    '''
    Check the refs and return a list of the ones which can be used as salt
    environments.
    '''
    ret = set()
    remote = repo.remotes[0]
    stale_refs = _stale_refs_pygit2(repo)
    for ref in repo.listall_references():
        ref = re.sub('^refs/', '', ref)
        rtype, rspec = ref.split('/', 1)
        if rtype == 'remotes':
            if rspec not in stale_refs:
                parted = rspec.partition('/')
                rspec = parted[2] if parted[2] else parted[0]
                rspec = rspec.replace('/', '_')
                if rspec == base_branch:
                    rspec = 'base'
                if _env_is_exposed(rspec):
                    ret.add(rspec)
        elif rtype == 'tags' and _env_is_exposed(rspec):
            ret.add(rspec)
    return ret


def _envs_dulwich(repo, base_branch):
    '''
    Check the refs and return a list of the ones which can be used as salt
    environments.
    '''
    ret = set()
    for ref in _dulwich_env_refs(repo.get_refs()):
        # ref will be something like 'refs/heads/master'
        rtype, rspec = ref[5:].split('/', 1)
        rspec = rspec.replace('/', '_')
        if rtype == 'heads':
            if rspec == base_branch:
                rspec = 'base'
            if _env_is_exposed(rspec):
                ret.add(rspec)
        elif rtype == 'tags' and _env_is_exposed(rspec):
            ret.add(rspec)
    return ret


def find_file(path, tgt_env='base', **kwargs):
    '''
    Find the first file to match the path and ref, read the file out of git
    and send the path to the newly cached file
    '''
    fnd = {'path': '',
           'rel': ''}
    if os.path.isabs(path):
        return fnd

    provider = _get_provider()
    base_branch = __opts__['gitfs_base']
    gitfs_root = __opts__['gitfs_root']
    gitfs_mountpoint = salt.utils.strip_proto(__opts__['gitfs_mountpoint'])
    if tgt_env == 'base':
        tgt_env = base_branch
    dest = os.path.join(__opts__['cachedir'], 'gitfs/refs', tgt_env, path)
    hashes_glob = os.path.join(__opts__['cachedir'],
                               'gitfs/hash',
                               tgt_env,
                               '{0}.hash.*'.format(path))
    blobshadest = os.path.join(__opts__['cachedir'],
                               'gitfs/hash',
                               tgt_env,
                               '{0}.hash.blob_sha1'.format(path))
    lk_fn = os.path.join(__opts__['cachedir'],
                         'gitfs/hash',
                         tgt_env,
                         '{0}.lk'.format(path))
    destdir = os.path.dirname(dest)
    hashdir = os.path.dirname(blobshadest)
    if not os.path.isdir(destdir):
        os.makedirs(destdir)
    if not os.path.isdir(hashdir):
        os.makedirs(hashdir)

    for repo_conf in init():
        repo = repo_conf['repo']
        root = repo_conf['root'] if repo_conf['root'] is not None \
            else gitfs_root
        mountpoint = repo_conf['mountpoint'] \
            if repo_conf['mountpoint'] is not None \
            else gitfs_mountpoint
        if mountpoint and not path.startswith(mountpoint + os.path.sep):
            continue
        repo_path = path[len(mountpoint):].lstrip(os.path.sep)
        if root:
            repo_path = os.path.join(root, repo_path)

        if provider == 'gitpython':
            tree = _get_tree_gitpython(repo, tgt_env)
            if not tree:
                # Branch/tag/SHA not found in repo, try the next
                continue
            try:
                blob = tree / repo_path
            except KeyError:
                continue
            blob_hexsha = blob.hexsha

        elif provider == 'pygit2':
            tree = _get_tree_pygit2(repo, tgt_env)
            if not tree:
                # Branch/tag/SHA not found in repo, try the next
                continue
            try:
                blob = repo[tree[repo_path].oid]
            except KeyError:
                continue
            blob_hexsha = blob.hex

        elif provider == 'dulwich':
            prefix_dirs, _, filename = repo_path.rpartition(os.path.sep)
            tree = _get_tree_dulwich(repo, tgt_env)
            tree = _dulwich_walk_tree(repo, tree, prefix_dirs)
            if not isinstance(tree, dulwich.objects.Tree):
                # Branch/tag/SHA not found in repo, try the next
                continue
            try:
                # Referencing the path in the tree returns a tuple, the
                # second element of which is the object ID of the blob
                blob = repo.get_object(tree[filename][1])
            except KeyError:
                continue
            blob_hexsha = blob.sha().hexdigest()

        salt.fileserver.wait_lock(lk_fn, dest)
        if os.path.isfile(blobshadest) and os.path.isfile(dest):
            with salt.utils.fopen(blobshadest, 'r') as fp_:
                sha = fp_.read()
                if sha == blob_hexsha:
                    fnd['rel'] = path
                    fnd['path'] = dest
                    return fnd
        with salt.utils.fopen(lk_fn, 'w+') as fp_:
            fp_.write('')
        for filename in glob.glob(hashes_glob):
            try:
                os.remove(filename)
            except Exception:
                pass
        with salt.utils.fopen(dest, 'w+') as fp_:
            if provider == 'gitpython':
                blob.stream_data(fp_)
            elif provider == 'pygit2':
                fp_.write(blob.data)
            elif provider == 'dulwich':
                fp_.write(blob.as_raw_string())
        with salt.utils.fopen(blobshadest, 'w+') as fp_:
            fp_.write(blob_hexsha)
        try:
            os.remove(lk_fn)
        except (OSError, IOError):
            pass
        fnd['rel'] = path
        fnd['path'] = dest
        return fnd
    return fnd


def serve_file(load, fnd):
    '''
    Return a chunk from a file based on the data received
    '''
    if 'env' in load:
        salt.utils.warn_until(
            'Boron',
            'Passing a salt environment should be done using \'saltenv\' '
            'not \'env\'. This functionality will be removed in Salt Boron.'
        )
        load['saltenv'] = load.pop('env')

    ret = {'data': '',
           'dest': ''}
    if 'path' not in load or 'loc' not in load or 'saltenv' not in load:
        return ret
    if not fnd['path']:
        return ret
    ret['dest'] = fnd['rel']
    gzip = load.get('gzip', None)
    with salt.utils.fopen(fnd['path'], 'rb') as fp_:
        fp_.seek(load['loc'])
        data = fp_.read(__opts__['file_buffer_size'])
        if gzip and data:
            data = salt.utils.gzip_util.compress(data, gzip)
            ret['gzip'] = gzip
        ret['data'] = data
    return ret


def file_hash(load, fnd):
    '''
    Return a file hash, the hash type is set in the master config file
    '''
    if 'env' in load:
        salt.utils.warn_until(
            'Boron',
            'Passing a salt environment should be done using \'saltenv\' '
            'not \'env\'. This functionality will be removed in Salt Boron.'
        )
        load['saltenv'] = load.pop('env')

    if 'path' not in load or 'saltenv' not in load:
        return ''
    ret = {'hash_type': __opts__['hash_type']}
    short = load['saltenv']
    base_branch = __opts__['gitfs_base']
    if short == 'base':
        short = base_branch
    relpath = fnd['rel']
    path = fnd['path']
    hashdest = os.path.join(__opts__['cachedir'],
                            'gitfs/hash',
                            short,
                            '{0}.hash.{1}'.format(relpath,
                                                  __opts__['hash_type']))
    if not os.path.isfile(hashdest):
        with salt.utils.fopen(path, 'rb') as fp_:
            ret['hsum'] = getattr(hashlib, __opts__['hash_type'])(
                fp_.read()).hexdigest()
        with salt.utils.fopen(hashdest, 'w+') as fp_:
            fp_.write(ret['hsum'])
        return ret
    else:
        with salt.utils.fopen(hashdest, 'rb') as fp_:
            ret['hsum'] = fp_.read()
        return ret


def _file_lists(load, form):
    '''
    Return a dict containing the file lists for files and dirs
    '''
    if 'env' in load:
        salt.utils.warn_until(
            'Boron',
            'Passing a salt environment should be done using \'saltenv\' '
            'not \'env\'. This functionality will be removed in Salt Boron.'
        )
        load['saltenv'] = load.pop('env')

    list_cachedir = os.path.join(__opts__['cachedir'], 'file_lists/gitfs')
    if not os.path.isdir(list_cachedir):
        try:
            os.makedirs(list_cachedir)
        except os.error:
            log.critical('Unable to make cachedir {0}'.format(list_cachedir))
            return []
    list_cache = os.path.join(list_cachedir, '{0}.p'.format(load['saltenv']))
    w_lock = os.path.join(list_cachedir, '.{0}.w'.format(load['saltenv']))
    cache_match, refresh_cache, save_cache = \
        salt.fileserver.check_file_list_cache(
            __opts__, form, list_cache, w_lock
        )
    if cache_match is not None:
        return cache_match
    if refresh_cache:
        ret = {}
        ret['files'] = _get_file_list(load)
        ret['dirs'] = _get_dir_list(load)
        if save_cache:
            salt.fileserver.write_file_list_cache(
                __opts__, ret, list_cache, w_lock
            )
        return ret.get(form, [])
    # Shouldn't get here, but if we do, this prevents a TypeError
    return []


def file_list(load):
    '''
    Return a list of all files on the file server in a specified
    environment
    '''
    return _file_lists(load, 'files')


def _get_file_list(load):
    '''
    Return a list of all files on the file server in a specified
    environment
    '''
    if 'env' in load:
        salt.utils.warn_until(
            'Boron',
            'Passing a salt environment should be done using \'saltenv\' '
            'not \'env\'. This functionality will be removed in Salt Boron.'
        )
        load['saltenv'] = load.pop('env')

    base_branch = __opts__['gitfs_base']
    gitfs_root = __opts__['gitfs_root']
    gitfs_mountpoint = salt.utils.strip_proto(__opts__['gitfs_mountpoint'])
    provider = _get_provider()
    if 'saltenv' not in load:
        return []
    if load['saltenv'] == 'base':
        load['saltenv'] = base_branch
    ret = set()
    for repo_conf in init():
        repo = repo_conf['repo']
        root = repo_conf['root'] if repo_conf['root'] is not None \
            else gitfs_root
        mountpoint = repo_conf['mountpoint'] \
            if repo_conf['mountpoint'] is not None \
            else gitfs_mountpoint

        if provider == 'gitpython':
            ret.update(
                _file_list_gitpython(repo, load['saltenv'], root, mountpoint)
            )
        elif provider == 'pygit2':
            ret.update(
                _file_list_pygit2(repo, load['saltenv'], root, mountpoint)
            )
        elif provider == 'dulwich':
            ret.update(
                _file_list_dulwich(repo, load['saltenv'], root, mountpoint)
            )
    return sorted(ret)


def _file_list_gitpython(repo, tgt, root, mountpoint):
    '''
    Get file list using GitPython
    '''
    ret = set()
    tree = _get_tree_gitpython(repo, tgt)
    if not tree:
        return ret
    if root:
        try:
            tree = tree / root
        except KeyError:
            return ret
    for blob in tree.traverse():
        if not isinstance(blob, git.Blob):
            continue
        if root:
            path = os.path.relpath(blob.path, root)
        else:
            path = blob.path
        ret.add(os.path.join(mountpoint, path))
    return ret


def _file_list_pygit2(repo, ref_tgt, root, mountpoint):
    '''
    Get file list using pygit2
    '''
    def _traverse(tree, repo, blobs, prefix):
        '''
        Traverse through a pygit2 Tree object recursively, accumulating all the
        blob paths within it in the "blobs" list
        '''
        for entry in iter(tree):
            blob = repo[entry.oid]
            if isinstance(blob, pygit2.Blob):
                blobs.append(os.path.join(prefix, entry.name))
            elif isinstance(blob, pygit2.Tree):
                _traverse(blob, repo, blobs, os.path.join(prefix, entry.name))
    ret = set()
    tree = _get_tree_pygit2(repo, ref_tgt)
    if not tree:
        return ret
    if root:
        try:
            # This might need to be changed to account for a root that
            # spans more than one directory
            tree = repo[tree[root].oid]
        except KeyError:
            return ret
        if not isinstance(tree, pygit2.Tree):
            return ret
    blobs = []
    if len(tree):
        _traverse(tree, repo, blobs, root)
    for blob in blobs:
        if root:
            blob = os.path.relpath(blob, root)
        ret.add(os.path.join(mountpoint, blob))
    return ret


def _file_list_dulwich(repo, ref_tgt, root, mountpoint):
    '''
    Get file list using dulwich
    '''
    def _traverse(tree, repo, blobs, prefix):
        '''
        Traverse through a dulwich Tree object recursively, accumulating all the
        blob paths within it in the "blobs" list
        '''
        for item in tree.items():
            obj = repo.get_object(item.sha)
            if isinstance(obj, dulwich.objects.Blob):
                blobs.append(os.path.join(prefix, item.path))
            elif isinstance(obj, dulwich.objects.Tree):
                _traverse(obj, repo, blobs, os.path.join(prefix, item.path))
    ret = set()
    tree = _get_tree_dulwich(repo, ref_tgt)
    tree = _dulwich_walk_tree(repo, tree, root)
    if not isinstance(tree, dulwich.objects.Tree):
        return ret
    blobs = []
    if len(tree):
        _traverse(tree, repo, blobs, root)
    for blob in blobs:
        if root:
            blob = os.path.relpath(blob, root)
        ret.add(os.path.join(mountpoint, blob))
    return ret


def file_list_emptydirs(load):
    '''
    Return a list of all empty directories on the master
    '''
    # Cannot have empty dirs in git
    return []


def dir_list(load):
    '''
    Return a list of all directories on the master
    '''
    return _file_lists(load, 'dirs')


def _get_dir_list(load):
    '''
    Get a list of all directories on the master
    '''
    if 'env' in load:
        salt.utils.warn_until(
            'Boron',
            'Passing a salt environment should be done using \'saltenv\' '
            'not \'env\'. This functionality will be removed in Salt Boron.'
        )
        load['saltenv'] = load.pop('env')

    base_branch = __opts__['gitfs_base']
    gitfs_root = __opts__['gitfs_root']
    gitfs_mountpoint = salt.utils.strip_proto(__opts__['gitfs_mountpoint'])
    provider = _get_provider()
    if 'saltenv' not in load:
        return []
    if load['saltenv'] == 'base':
        load['saltenv'] = base_branch
    ret = set()
    for repo_conf in init():
        repo = repo_conf['repo']
        root = repo_conf['root'] if repo_conf['root'] is not None \
            else gitfs_root
        mountpoint = repo_conf['mountpoint'] \
            if repo_conf['mountpoint'] is not None \
            else gitfs_mountpoint

        if provider == 'gitpython':
            ret.update(
                _dir_list_gitpython(repo, load['saltenv'], root, mountpoint)
            )
        elif provider == 'pygit2':
            ret.update(
                _dir_list_pygit2(repo, load['saltenv'], root, mountpoint)
            )
        elif provider == 'dulwich':
            ret.update(
                _dir_list_dulwich(repo, load['saltenv'], root, mountpoint)
            )
    return sorted(ret)


def _dir_list_gitpython(repo, tgt, root, mountpoint):
    '''
    Get list of directories using GitPython
    '''
    ret = set()
    tree = _get_tree_gitpython(repo, tgt)
    if not tree:
        return ret
    if root:
        try:
            tree = tree / root
        except KeyError:
            return ret
    for blob in tree.traverse():
        if not isinstance(blob, git.Tree):
            continue
        if root:
            path = os.path.relpath(blob.path, root)
        else:
            path = blob.path
        ret.add(os.path.join(mountpoint, path))
    return ret


def _dir_list_pygit2(repo, ref_tgt, root, mountpoint):
    '''
    Get a list of directories using pygit2
    '''
    def _traverse(tree, repo, blobs, prefix):
        '''
        Traverse through a pygit2 Tree object recursively, accumulating all the
        empty directories within it in the "blobs" list
        '''
        for entry in iter(tree):
            blob = repo[entry.oid]
            if not isinstance(blob, pygit2.Tree):
                continue
            blobs.append(os.path.join(prefix, entry.name))
            if len(blob):
                _traverse(blob, repo, blobs, os.path.join(prefix, entry.name))
    ret = set()
    tree = _get_tree_pygit2(repo, ref_tgt)
    if not tree:
        return ret
    if root:
        try:
            tree = repo[tree[root].oid]
        except KeyError:
            return ret
        if not isinstance(tree, pygit2.Tree):
            return ret
    blobs = []
    if len(tree):
        _traverse(tree, repo, blobs, root)
    for blob in blobs:
        if root:
            blob = os.path.relpath(blob, root)
        ret.add(os.path.join(mountpoint, blob))
    return ret


def _dir_list_dulwich(repo, ref_tgt, root, mountpoint):
    '''
    Get a list of directories using pygit2
    '''
    def _traverse(tree, repo, blobs, prefix):
        '''
        Traverse through a dulwich Tree object recursively, accumulating all
        the empty directories within it in the "blobs" list
        '''
        for item in tree.items():
            obj = repo.get_object(item.sha)
            if not isinstance(obj, dulwich.objects.Tree):
                continue
            blobs.append(os.path.join(prefix, item.path))
            if len(repo.get_object(item.sha)):
                _traverse(obj, repo, blobs, os.path.join(prefix, item.path))
    ret = set()
    tree = _get_tree_dulwich(repo, ref_tgt)
    tree = _dulwich_walk_tree(repo, tree, root)
    if not isinstance(tree, dulwich.objects.Tree):
        return ret
    blobs = []
    if len(tree):
        _traverse(tree, repo, blobs, root)
    for blob in blobs:
        if root:
            blob = os.path.relpath(blob, root)
        ret.add(os.path.join(mountpoint, blob))
    return ret
