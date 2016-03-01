#!/usr/bin/env python

import os

import networkx as nx
import click
from solar.core.resource import composer as cr
from solar.core import resource
from solar.dblayer.model import ModelMeta
from solar import events as evapi
from fuelclient.objects.environment import Environment


@click.group()
def main():
    pass


class NailgunSource(object):

    def nodes(self, uids):
        from fuelclient.objects.node import Node
        nodes_obj = map(Node, uids)
        return [(str(n.data['id']), str(n.data['ip']), str(n.data['cluster']))
                for n in nodes_obj]

    def roles(self, uid):
        from fuelclient.objects.node import Node
        from fuelclient.objects.environment import Environment
        node = Node(uid)
        env = Environment(node.data['cluster'])
        facts = env.get_default_facts('deployment', [uid])
        return [f['role'] for f in facts]

    def master(self):
        return 'master', '10.20.0.2'

    def graph(self, uid):
        from fuelclient.client import APIClient as nailgun
        return nailgun.get_request('clusters/{}/serialized_tasks'.format(uid))


class DumbSource(object):

    def nodes(self, uids):
        ip_mask = '10.0.0.%s'
        return [(int(uid), ip_mask % uid, 1) for uid in uids]

    def roles(self, uid):
        return ['primary-controller']

    def master(self):
        return 'master', '0.0.0.0'

if os.environ.get('DEBUG_FSCLIENT'):
    source = DumbSource()
else:
    source = NailgunSource()


@main.command()
@click.argument('uids', nargs=-1)
def nodes(uids):
    for uid, ip, env in source.nodes(uids):
        cr.create('fuel_node', 'f2s/fuel_node',
                  {'index': int(uid), 'ip': ip})


@main.command()
@click.argument('env')
def master(env):
    master = source.master()
    cr.create('master', 'f2s/fuel_node',
              {'index': master[0], 'ip': master[1]})

    cr.create('genkeys', 'f2s/genkeys', {
        'node': 'node'+master[0],
        'index': int(env)})


def dep_name(dep):
    if dep.get('node_id', None) is None:
        return dep['name']
    else:
        return '{}_{}'.format(dep['name'], dep['node_id'])


def create(*args, **kwargs):
    try:
        cr.create(*args, **kwargs)
    except Exception as exc:
        print exc


@main.command()
@click.argument('env')
@click.argument('node')
def alloc(env, node):
    dg = nx.DiGraph()
    response = source.graph(env)
    graph = response['tasks_graph']
    directory = response['tasks_directory']
    for task in graph[node]:
        meta = directory[task['id']]
        if node == 'null':
            name = task['id']
        else:
            name = '{}_{}'.format(task['id'], node)

        if task['type'] == 'skipped':
            create(name, 'f2s/noop')
        elif task['type'] == 'shell':
            create(name, 'f2s/command', {
                'cmd': meta['parameters']['cmd'],
                'timeout': meta['parameters'].get('timeout', 30)})
        elif task['type'] == 'sync':
            create(name, 'resources/sources',
                   {'sources': [meta['parameters']]})
        elif task['type'] == 'copy_files':
            create(name, 'resources/sources',
                   {'sources': meta['parameters']['files']})
        elif task['type'] == 'puppet':
            create(name, 'f2s/' + task['id'])
        else:
            print 'Unknown task type %s' % task
        for dep in task.get('requires', []):
            dg.add_edge(dep_name(dep), name)
        for dep in task.get('required_for', []):
            dg.add_edge(name, dep_name(dep))
    for u, v in dg.edges():
        if u == v:
            continue
        try:
            evapi.add_dep(u, v, actions=('run', 'update'))
        except Exception as exc:
            print exc


@main.command()
@click.argument('env_id', type=click.INT)
@click.argument('uids', nargs=-1)
def prep(env_id, uids):
    for uid in uids:
        node = resource.load('node{}'.format(uid))
        res = cr.create('fuel_data{}'.format(uid), 'f2s/fuel_data',
                        {'uid': uid, 'env': env_id})
        node = resource.load('node{}'.format(uid))
        node.connect(res[0], {})


@main.command()
@click.argument('uids', nargs=-1)
def roles(uids):
    for uid, ip, env in source.nodes(uids):
        for role in source.roles(uid):
            cr.create(role, 'f2s/role_'+role,
                      {'index': uid, 'env': env, 'node': 'node'+str(uid)})


@main.command()
@click.argument('env_id', type=click.INT)
@click.argument('uids', nargs=-1)
def prefetch(env_id, uids):
    env = Environment(env_id)
    facts = env.get_default_facts('deployment', uids)
    facts = {node['uid']: node for node in facts}
    for uid in uids:
        res = resource.load('fuel_data{}'.format(uid))
        node_facts = facts[uid]
        res_args = res.args
        for key in node_facts.keys():
            if key not in res_args:
                res.input_add(key)
        res.update(node_facts)


if __name__ == '__main__':
    main()
    ModelMeta.session_end()
