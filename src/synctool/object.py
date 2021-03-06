#
#   synctool.object.py    WJ110
#
#   synctool Copyright 2014 Walter de Jong <walter@heiho.net>
#
#   synctool COMES WITH NO WARRANTY. synctool IS FREE SOFTWARE.
#   synctool is distributed under terms described in the GNU General Public
#   License.
#

'''a SyncObject is a source file + matching destination path and attributes'''

import os
import stat
import time
import shutil
import hashlib

import synctool.lib
from synctool.lib import verbose, stdout, stderr, terse, unix_out, log
from synctool.lib import dryrun_msg, prettypath
import synctool.param
import synctool.syncstat

# size for doing I/O while checksumming files
IO_SIZE = 16 * 1024


class VNode(object):
    '''base class for doing actions with directory entries'''

    def __init__(self, filename, statbuf, exists):
        '''filename is typically destination path
        statbuf is source statbuf
        exists is boolean whether dest path already exists'''

        self.name = filename
        self.stat = statbuf
        self.exists = exists


    def typename(self):
        '''return file type as human readable string'''
        return '(unknown file type)'


    def move_saved(self):
        '''move existing entry to .saved'''

        verbose(dryrun_msg('saving %s as %s.saved' % (self.name, self.name)))
        unix_out('mv %s %s.saved' % (self.name, self.name))

        if not synctool.lib.DRY_RUN:
            verbose('  os.rename(%s, %s.saved)' % (self.name, self.name))
            try:
                os.rename(self.name, '%s.saved' % self.name)
            except OSError as err:
                stderr('failed to save %s as %s.saved : %s' % (self.name,
                                                               self.name,
                                                               err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'save %s.saved' % self.name)


    def harddelete(self):
        '''delete existing entry'''

        if synctool.lib.DRY_RUN:
            not_str = 'not '
        else:
            not_str = ''

        stdout('%sdeleting %s' % (not_str, self.name))
        unix_out('rm %s' % self.name)
        terse(synctool.lib.TERSE_DELETE, self.name)

        if not synctool.lib.DRY_RUN:
            verbose('  os.unlink(%s)' % self.name)
            try:
                os.unlink(self.name)
            except OSError as err:
                stderr('failed to delete %s : %s' % (self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'delete %s' % self.name)
            else:
                log('deleted %s' % self.name)


    def quiet_delete(self):
        '''silently delete existing entry; only called by fix()'''

        if not synctool.lib.DRY_RUN and not synctool.param.BACKUP_COPIES:
            verbose('  os.unlink(%s)' % self.name)
            try:
                os.unlink(self.name)
            except OSError:
                pass


    def mkdir_basepath(self):
        '''call mkdir -p to create leading path'''

        if synctool.lib.DRY_RUN:
            return

        basedir = os.path.dirname(self.name)

        # be a bit quiet about it
        if synctool.lib.VERBOSE or synctool.lib.UNIX_CMD:
            verbose('making directory %s' % prettypath(basedir))
            unix_out('mkdir -p %s' % basedir)

        synctool.lib.mkdir_p(basedir)


    def compare(self, src_path, dest_stat):
        '''compare content
        Return True when same, False when different'''
        return True


    def create(self):
        '''create a new entry'''
        pass


    def fix(self):
        '''repair the existing entry
        set owner and permissions equal to source'''

        if self.exists:
            if synctool.param.BACKUP_COPIES:
                self.move_saved()
            else:
                self.quiet_delete()

        self.mkdir_basepath()
        self.create()
        self.set_owner()
        self.set_permissions()


    def set_owner(self):
        '''set ownership equal to source'''

        verbose(dryrun_msg('  os.chown(%s, %d, %d)' %
                           (self.name, self.stat.uid, self.stat.gid)))
        unix_out('chown %s.%s %s' % (self.stat.ascii_uid(),
                                     self.stat.ascii_gid(), self.name))
        if not synctool.lib.DRY_RUN:
            try:
                os.chown(self.name, self.stat.uid, self.stat.gid)
            except OSError as err:
                stderr('failed to chown %s.%s %s : %s' %
                       (self.stat.ascii_uid(), self.stat.ascii_gid(),
                        self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'owner %s' % self.name)


    def set_permissions(self):
        '''set access permission bits equal to source'''

        verbose(dryrun_msg('  os.chmod(%s, %04o)' %
                           (self.name, self.stat.mode & 07777)))
        unix_out('chmod 0%o %s' % (self.stat.mode & 07777, self.name))
        if not synctool.lib.DRY_RUN:
            try:
                os.chmod(self.name, self.stat.mode & 07777)
            except OSError as err:
                stderr('failed to chmod %04o %s : %s' %
                       (self.stat.mode & 07777, self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'mode %s' % self.name)


    def set_times(self, atime, mtime):
        '''set access and mod times'''

        # only used for purge --single

        if not synctool.lib.DRY_RUN:
            try:
                os.utime(self.name, (atime, mtime))
            except OSError as err:
                stderr('failed to set utime on %s : %s' % (self.name,
                                                           err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'utime %s' % self.name)


class VNodeFile(VNode):
    '''vnode for a regular file'''

    def __init__(self, filename, statbuf, exists, src_path):
        super(VNodeFile, self).__init__(filename, statbuf, exists)

        self.src_path = src_path


    def typename(self):
        '''return file type as human readable string'''
        return 'regular file'


    def compare(self, src_path, dest_stat):
        '''see if files are the same
        Return True if the same'''

        if self.stat.size != dest_stat.size:
            if synctool.lib.DRY_RUN:
                stdout('%s mismatch (file size)' % self.name)
            else:
                stdout('%s updated (file size mismatch)' % self.name)
            terse(synctool.lib.TERSE_SYNC, self.name)
            unix_out('# updating file %s' % self.name)
            return False

        return self._compare_checksums(src_path)


    def _compare_checksums(self, src_path):
        '''compare checksum of src_path and dest: self.name
        Return True if the same'''

        try:
            f1 = open(src_path, 'rb')
        except IOError as err:
            stderr('error: failed to open %s : %s' % (src_path, err.strerror))
            # return True because we can't fix an error in src_path
            return True

        sum1 = hashlib.md5()
        sum2 = hashlib.md5()

        with f1:
            try:
                f2 = open(self.name, 'rb')
            except IOError as err:
                stderr('error: failed to open %s : %s' % (self.name,
                                                          err.strerror))
                return False

            with f2:
                ended = False
                while not ended and (sum1.digest() == sum2.digest()):
                    try:
                        data1 = f1.read(IO_SIZE)
                    except IOError as err:
                        stderr('error reading file %s: %s' % (src_path,
                                                              err.strerror))
                        return False

                    if not data1:
                        ended = True
                    else:
                        sum1.update(data1)

                    try:
                        data2 = f2.read(IO_SIZE)
                    except IOError as err:
                        stderr('error reading file %s: %s' % (self.name,
                                                              err.strerror))
                        return False

                    if not data2:
                        ended = True
                    else:
                        sum2.update(data2)

        if sum1.digest() != sum2.digest():
            if synctool.lib.DRY_RUN:
                stdout('%s mismatch (MD5 checksum)' % self.name)
            else:
                stdout('%s updated (MD5 mismatch)' % self.name)

            unix_out('# updating file %s' % self.name)
            terse(synctool.lib.TERSE_SYNC, self.name)
            return False

        return True


    def create(self):
        '''copy file'''

        if not self.exists:
            terse(synctool.lib.TERSE_NEW, self.name)

        verbose(dryrun_msg('  copy %s %s' % (self.src_path, self.name)))
        unix_out('cp %s %s' % (self.src_path, self.name))

        if not synctool.lib.DRY_RUN:
            try:
                # copy file
                shutil.copy(self.src_path, self.name)
            except IOError as err:
                stderr('failed to copy %s to %s: %s' %
                       (prettypath(self.src_path), self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, self.name)


class VNodeDir(VNode):
    '''vnode for a directory'''

    def __init__(self, filename, statbuf, exists):
        super(VNodeDir, self).__init__(filename, statbuf, exists)


    def typename(self):
        '''return file type as human readable string'''
        return 'directory'


    def create(self):
        '''create directory'''

        if os.path.exists(self.name):
            # it can happen that the dir already exists
            # due to recursion in visit() + VNode.mkdir_basepath()
            # So this is double checked for dirs that did not exist
            return

        verbose(dryrun_msg('  os.mkdir(%s)' % self.name))
        unix_out('mkdir %s' % self.name)
        terse(synctool.lib.TERSE_MKDIR, self.name)

        if not synctool.lib.DRY_RUN:
            try:
                os.mkdir(self.name, self.stat.mode & 07777)
            except OSError as err:
                stderr('failed to make directory %s : %s' % (self.name,
                                                             err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'mkdir %s' % self.name)


    def harddelete(self):
        '''delete directory'''

        if synctool.lib.DRY_RUN:
            not_str = 'not '
        else:
            not_str = ''

        stdout('%sremoving %s' % (not_str, self.name + os.sep))
        unix_out('rmdir %s' % self.name)
        terse(synctool.lib.TERSE_DELETE, self.name + os.sep)

        if not synctool.lib.DRY_RUN and not synctool.param.BACKUP_COPIES:
            verbose('  os.rmdir(%s)' % self.name)
            try:
                os.rmdir(self.name)
            except OSError:
                # probably directory not empty
                # refuse to delete dir, just move it aside
                verbose('refusing to delete directory %s' % self.name)
                self.move_saved()


    def quiet_delete(self):
        '''silently delete directory; only called by fix()'''

        if not synctool.lib.DRY_RUN and not synctool.param.BACKUP_COPIES:
            verbose('  os.rmdir(%s)' % self.name)
            try:
                os.rmdir(self.name)
            except OSError:
                # probably directory not empty
                # refuse to delete dir, just move it aside
                verbose('refusing to delete directory %s' % self.name)
                self.move_saved()


class VNodeLink(VNode):
    '''vnode for a symbolic link'''

    def __init__(self, filename, statbuf, exists, oldpath):
        super(VNodeLink, self).__init__(filename, statbuf, exists)
        self.oldpath = oldpath


    def typename(self):
        '''return file type as human readable string'''
        return 'symbolic link'


    def compare(self, src_path, dest_stat):
        '''compare symbolic links'''

        if not self.exists:
            return False

        try:
            link_to = os.readlink(self.name)
        except OSError as err:
            stderr('error reading symlink %s : %s' % (self.name,
                                                      err.strerror))
            return False

        if self.oldpath != link_to:
            stdout('%s should point to %s, but points to %s' %
                   (self.name, self.oldpath, link_to))
            terse(synctool.lib.TERSE_LINK, self.name)
            return False

        return True


    def create(self):
        '''create symbolic link'''

        verbose(dryrun_msg('  os.symlink(%s, %s)' % (self.oldpath,
                                                     self.name)))
        unix_out('ln -s %s %s' % (self.oldpath, self.name))
        terse(synctool.lib.TERSE_LINK, self.name)

        if not synctool.lib.DRY_RUN:
            try:
                os.symlink(self.oldpath, self.name)
            except OSError as err:
                stderr('failed to create symlink %s -> %s : %s' %
                       (self.name, self.oldpath, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'link %s' % self.name)


    def set_owner(self):
        '''set ownership of symlink'''

        if not hasattr(os, 'lchown'):
            # you never know
            return

        verbose(dryrun_msg('  os.lchown(%s, %d, %d)' %
                           (self.name, self.stat.uid, self.stat.gid)))
        unix_out('lchown %s.%s %s' % (self.stat.ascii_uid(),
                                      self.stat.ascii_gid(), self.name))
        if not synctool.lib.DRY_RUN:
            try:
                os.lchown(self.name, self.stat.uid, self.stat.gid)
            except OSError as err:
                stderr('failed to lchown %s.%s %s : %s' %
                       (self.stat.ascii_uid(), self.stat.ascii_gid(),
                        self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'owner %s' % self.name)


    def set_permissions(self):
        '''set permissions of symlink (if possible)'''

        # check if this platform supports lchmod()
        # Linux does not have lchmod: its symlinks are always mode 0777
        if not hasattr(os, 'lchmod'):
            return

        verbose(dryrun_msg('  os.lchmod(%s, %04o)' %
                           (self.name, self.stat.mode & 07777)))
        unix_out('lchmod 0%o %s' % (self.stat.mode & 07777, self.name))
        if not synctool.lib.DRY_RUN:
            try:
                os.lchmod(self.name, self.stat.mode & 07777)
            except OSError as err:
                stderr('failed to lchmod %04o %s : %s' %
                       (self.stat.mode & 07777, self.name, err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'mode %s' % self.name)


class VNodeFifo(VNode):
    '''vnode for a fifo'''

    def __init__(self, filename, statbuf, exists):
        super(VNodeFifo, self).__init__(filename, statbuf, exists)


    def typename(self):
        '''return file type as human readable string'''
        return 'fifo'


    def create(self):
        '''make a fifo'''

        verbose(dryrun_msg('  os.mkfifo(%s)' % self.name))
        unix_out('mkfifo %s' % self.name)
        terse(synctool.lib.TERSE_NEW, self.name)

        if not synctool.lib.DRY_RUN:
            try:
                os.mkfifo(self.name, self.stat.mode & 0777)
            except OSError as err:
                stderr('failed to create fifo %s : %s' % (self.name,
                                                          err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'fifo %s' % self.name)


class VNodeChrDev(VNode):
    '''vnode for a character device file'''

    def __init__(self, filename, syncstat_obj, exists, src_stat):
        super(VNodeChrDev, self).__init__(filename, syncstat_obj, exists)
        self.src_stat = src_stat


    def typename(self):
        '''return file type as human readable string'''
        return 'character device file'


    def compare(self, src_path, dest_stat):
        '''see if devs are the same'''

        if not self.exists:
            return False

        # dest_stat is a SyncStat object and it's useless here
        # I need a real, fresh statbuf that includes st_rdev field
        try:
            dest_stat = os.lstat(self.name)
        except OSError as err:
            stderr('error checking %s : %s' % (self.name, err.strerror))
            return False

        src_major = os.major(self.src_stat.st_rdev)
        src_minor = os.minor(self.src_stat.st_rdev)
        dest_major = os.major(dest_stat.st_rdev)
        dest_minor = os.minor(dest_stat.st_rdev)
        if src_major != dest_major or src_minor != dest_minor:
            stdout('%s should have major,minor %d,%d but has %d,%d' %
                   (self.name, src_major, src_minor, dest_major, dest_minor))
            unix_out('# updating major,minor %s' % self.name)
            terse(synctool.lib.TERSE_SYNC, self.name)
            return False

        return True


    def create(self):
        '''make a character device file'''

        major = os.major(self.src_stat.st_rdev)
        minor = os.minor(self.src_stat.st_rdev)

        verbose(dryrun_msg('  os.mknod(%s, CHR %d,%d)' % (self.name, major,
                                                          minor)))
        unix_out('mknod %s c %d %d' % (self.name, major, minor))
        terse(synctool.lib.TERSE_NEW, self.name)

        if not synctool.lib.DRY_RUN:
            try:
                os.mknod(self.name,
                         (self.src_stat.st_mode & 0777) | stat.S_IFCHR,
                         os.makedev(major, minor))
            except OSError as err:
                stderr('failed to create device %s : %s' % (self.name,
                                                            err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'device %s' % self.name)


class VNodeBlkDev(VNode):
    '''vnode for a block device file'''

    def __init__(self, filename, syncstat_obj, exists, src_stat):
        super(VNodeBlkDev, self).__init__(filename, syncstat_obj, exists)
        self.src_stat = src_stat


    def typename(self):
        '''return file type as human readable string'''
        return 'block device file'


    def compare(self, src_path, dest_stat):
        '''see if devs are the same'''

        if not self.exists:
            return False

        # dest_stat is a SyncStat object and it's useless here
        # I need a real, fresh statbuf that includes st_rdev field
        try:
            dest_stat = os.lstat(self.name)
        except OSError as err:
            stderr('error checking %s : %s' % (self.name, err.strerror))
            return False

        src_major = os.major(self.src_stat.st_rdev)
        src_minor = os.minor(self.src_stat.st_rdev)
        dest_major = os.major(dest_stat.st_rdev)
        dest_minor = os.minor(dest_stat.st_rdev)
        if src_major != dest_major or src_minor != dest_minor:
            stdout('%s should have major,minor %d,%d but has %d,%d' %
                (self.name, src_major, src_minor, dest_major, dest_minor))
            unix_out('# updating major,minor %s' % self.name)
            terse(synctool.lib.TERSE_SYNC, self.name)
            return False

        return True


    def create(self):
        '''make a block device file'''

        major = os.major(self.src_stat.st_rdev)
        minor = os.minor(self.src_stat.st_rdev)

        verbose(dryrun_msg('  os.mknod(%s, BLK %d,%d)' % (self.name, major,
                                                          minor)))
        unix_out('mknod %s b %d %d' % (self.name, major, minor))
        terse(synctool.lib.TERSE_NEW, self.name)

        if not synctool.lib.DRY_RUN:
            try:
                os.mknod(self.name,
                         (self.src_stat.st_mode & 0777) | stat.S_IFBLK,
                         os.makedev(major, minor))
            except OSError as err:
                stderr('failed to create device %s : %s' % (self.name,
                                                            err.strerror))
                terse(synctool.lib.TERSE_FAIL, 'device %s' % self.name)


class SyncObject(object):
    '''a class holding the source path (file in the repository)
    and the destination path (target file on the system).
    The SyncObject caches any stat info'''

    def __init__(self, src_name, dest_name, ov_type=0):
        '''src_name is simple filename without leading path
        dest_name is the src_name without group extension
        ov_type describes what overlay type the object has:
        OV_POST, OV_TEMPLATE, etc.'''

        # booleans is_post and no_ext are used by the overlay code

        self.src_path = src_name
        self.dest_path = dest_name
        self.ov_type = ov_type
        self.src_stat = self.dest_stat = None

    def make(self, src_dir, dest_dir):
        '''make() fills in the full paths and stat structures'''

        self.src_path = os.path.join(src_dir, self.src_path)
        self.src_stat = synctool.syncstat.SyncStat(self.src_path)
        self.dest_path = os.path.join(dest_dir, self.dest_path)
        self.dest_stat = synctool.syncstat.SyncStat(self.dest_path)

    def print_src(self):
        '''pretty print my source path'''

        if self.src_stat and self.src_stat.is_dir():
            return prettypath(self.src_path) + os.sep

        return prettypath(self.src_path)

    def __repr__(self):
        return '[<SyncObject>: (%s) (%s)]' % (self.src_path, self.dest_path)

    def check(self):
        '''check differences between src and dest,
        and fix it when not a dry run
        Return pair: updated, metadata_updated'''

        # src_path is under $overlay/
        # dest_path is in the filesystem

        vnode = None

        if not self.dest_stat.exists():
            stdout('%s does not exist' % self.dest_path)
            log('creating %s' % self.dest_path)
            vnode = self.vnode_obj()
            vnode.fix()
            return True, False

        src_type = self.src_stat.filetype()
        dest_type = self.dest_stat.filetype()
        if src_type != dest_type:
            # entry is of a different file type
            vnode = self.vnode_obj()
            stdout('%s should be a %s' % (self.dest_path, vnode.typename()))
            terse(synctool.lib.TERSE_WARNING, 'wrong type %s' %
                                               self.dest_path)
            log('fix type %s' % self.dest_path)
            vnode.fix()
            return True, False

        vnode = self.vnode_obj()
        if not vnode.compare(self.src_path, self.dest_stat):
            # content is different; change the entire object
            log('updating %s' % self.dest_path)
            vnode.fix()
            return True, False

        # check ownership and permissions
        # rectify if needed
        meta_updated = False
        if ((self.src_stat.uid != self.dest_stat.uid) or
            (self.src_stat.gid != self.dest_stat.gid)):
            stdout('%s should have owner %s.%s (%d.%d), '
                   'but has %s.%s (%d.%d)' % (self.dest_path,
                   self.src_stat.ascii_uid(),
                   self.src_stat.ascii_gid(),
                   self.src_stat.uid, self.src_stat.gid,
                   self.dest_stat.ascii_uid(),
                   self.dest_stat.ascii_gid(),
                   self.dest_stat.uid, self.dest_stat.gid))
            terse(synctool.lib.TERSE_OWNER, '%s.%s %s' %
                                            (self.src_stat.ascii_uid(),
                                             self.src_stat.ascii_gid(),
                                             self.dest_path))
            log('set owner %s.%s (%d.%d) %s' %
                (self.src_stat.ascii_uid(), self.src_stat.ascii_gid(),
                 self.src_stat.uid, self.src_stat.gid,
                 self.dest_path))
            vnode.set_owner()
            meta_updated = True

        if self.src_stat.mode != self.dest_stat.mode:
            stdout('%s should have mode %04o, but has %04o' %
                   (self.dest_path, self.src_stat.mode & 07777,
                    self.dest_stat.mode & 07777))
            terse(synctool.lib.TERSE_MODE, '%04o %s' %
                                           (self.src_stat.mode & 07777,
                                            self.dest_path))
            log('set mode %04o %s' % (self.src_stat.mode & 07777,
                                      self.dest_path))
            vnode.set_permissions()
            meta_updated = True

        # set owner/permissions do not trigger .post scripts
        return False, meta_updated

    def vnode_obj(self):
        '''create vnode object for this SyncObject'''

        exists = self.dest_stat.exists()

        if self.src_stat.is_file():
            return VNodeFile(self.dest_path, self.src_stat, exists,
                             self.src_path)

        if self.src_stat.is_dir():
            return VNodeDir(self.dest_path, self.src_stat, exists)

        if self.src_stat.is_link():
            try:
                oldpath = os.readlink(self.src_path)
            except OSError as err:
                stderr('error reading symlink %s : %s' % (self.print_src(),
                                                          err.strerror))
                terse(synctool.lib.TERSE_FAIL, self.src_path)
                return None

            return VNodeLink(self.dest_path, self.src_stat, exists, oldpath)

        if self.src_stat.is_fifo():
            return VNodeFifo(self.dest_path, self.src_stat, exists)

        if self.src_stat.is_chardev():
            return VNodeChrDev(self.dest_path, self.src_stat, exists,
                               os.stat(self.src_path))

        if self.src_stat.is_blockdev():
            return VNodeBlkDev(self.dest_path, self.src_stat, exists,
                               os.stat(self.src_path))

        # error, can not handle file type of src_path
        return None

    def vnode_dest_obj(self):
        '''create vnode object for this SyncObject's destination'''

        exists = self.dest_stat.exists()

        if self.dest_stat.is_file():
            return VNodeFile(self.dest_path, self.src_stat, exists,
                             self.src_path)

        if self.dest_stat.is_dir():
            return VNodeDir(self.dest_path, self.src_stat, exists)

        if self.dest_stat.is_link():
            try:
                oldpath = os.readlink(self.src_path)
            except OSError as err:
                stderr('error reading symlink %s : %s' % (self.print_src(),
                                                          err.strerror))
                terse(synctool.lib.TERSE_FAIL, self.src_path)
                return None

            return VNodeLink(self.dest_path, self.src_stat, exists, oldpath)

        if self.dest_stat.is_fifo():
            return VNodeFifo(self.dest_path, self.src_stat, exists)

        if self.dest_stat.is_chardev():
            return VNodeChrDev(self.dest_path, self.src_stat, exists,
                               os.stat(self.src_path))

        if self.dest_stat.is_blockdev():
            return VNodeBlkDev(self.dest_path, self.src_stat, exists,
                               os.stat(self.src_path))

        # error, can not handle file type of src_path
        return None

    def check_purge_timestamp(self):
        '''check timestamp between src and dest
        Returns True if same, False if not'''

        # This is only used for purge/
        # check() has already determined that the files are the same
        # Now only check the timestamp ...
        # Note that SyncStat objects do not know the timestamps;
        # they are not cached only to save memory
        # So now we have to os.stat() again to get the times; it is
        # not a big problem because this func is used for purge_single only

        # src_path is under $purge/
        # dest_path is in the filesystem

        try:
            src_stat = os.lstat(self.src_path)
        except OSError as err:
            stderr('error: stat(%s) failed: %s' % (self.src_path,
                                                   err.strerror))
            return False

        try:
            dest_stat = os.lstat(self.dest_path)
        except OSError as err:
            stderr('error: stat(%s) failed: %s' % (self.dest_path,
                                                   err.strerror))
            return False

        if src_stat.st_mtime > dest_stat.st_mtime:
            stdout('%s mismatch (only timestamp)' % self.dest_path)
            terse(synctool.lib.TERSE_WARNING,
                  '%s (only timestamp)' % self.dest_path)

            verbose(dryrun_msg('  os.utime(%s, %s)'
                               '' % (self.dest_path,
                                     time.ctime(src_stat.st_mtime))))
            unix_out('touch -r %s %s' % (self.src_path, self.dest_path))

            vnode = self.vnode_obj()
            vnode.set_times(src_stat.st_atime, src_stat.st_mtime)
            return False

        return True

# EOB
