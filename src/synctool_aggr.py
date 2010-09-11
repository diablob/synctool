#! /usr/bin/env python
#
#	synctool_aggr.py	WJ109
#
#	group together output that is the same
#
#   synctool by Walter de Jong <walter@heiho.net> (c) 2003-2010
#
#   synctool COMES WITH NO WARRANTY. synctool IS FREE SOFTWARE.
#   synctool is distributed under terms described in the GNU General Public
#   License.
#

import os
import sys
import string
import getopt

try:
	import subprocess
	use_subprocess = True
except ImportError:
	# well, your Python version is ancient
	use_subprocess = False


def aggregate(f):
	lines = f.readlines()
	if not lines:
		return

	lines = map(string.strip, lines)

	output_per_node = {}

	for line in lines:
		arr = string.split(line, ':')

		if len(arr) <= 1:
			print line
			continue

		node = arr[0]
		output = string.join(arr[1:], ':')

		if not output_per_node.has_key(node):
			output_per_node[node] = [output]
		else:
			output_per_node[node].append(output)

	nodes = output_per_node.keys()
	if not nodes:
		return

	nodes.sort()

	while len(nodes) > 0:
		node = nodes.pop(0)

		out = output_per_node[node]

		nodelist = [node]

		for node2 in nodes[:]:
			if out == output_per_node[node2]:
				nodelist.append(node2)
				del output_per_node[node2]
				nodes.remove(node2)

		print '%s:' % string.join(nodelist, ',')
		for line in out:
			print line


def run(cmd_args):
	'''pipe the output through the aggregator'''

#
#	simply re-run this command, but with a pipe
#
	if '-a' in cmd_args:
		cmd_args.remove('-a')

	if '--aggregate' in cmd_args:
		cmd_args.remove('--aggregate')

	if use_subprocess:
		f = subprocess.Popen(cmd_args, shell=False, env={'PATH' : os.getenv('PATH')},
			bufsize=4096, stdout=subprocess.PIPE).stdout
	else:
		f = os.popen(string.join(cmd_args), 'r')

	aggregate(f)
	f.close()


def usage():
	print 'Typical use of synctool-aggr is:'
	print
	print '  command | synctool-aggr'
	print
	print 'synctool-aggr is built in to synctool-master and synctool-ssh'
	print "and activated by the '-a' option"
	print
	print 'Written by Walter de Jong <walter@heiho.net> (c) 2009-2010'


def get_options():
	if len(sys.argv) <= 1:
		return

	try:
		opts, args = getopt.getopt(sys.argv[1:], 'h', ['help'])
	except getopt.error, (reason):
		print '%s: %s' % (os.path.basename(sys.argv[0]), reason)
#		usage()
		sys.exit(1)

	except getopt.GetoptError, (reason):
		print '%s: %s' % (os.path.basename(sys.argv[0]), reason)
#		usage()
		sys.exit(1)

	except:
		usage()
		sys.exit(1)

	for opt, arg in opts:
		if opt in ('-h', '--help', '-?'):
			usage()
			sys.exit(1)


if __name__ == '__main__':
	get_options()

	aggregate(sys.stdin)


# EOB
