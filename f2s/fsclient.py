#!/usr/bin/env python
#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

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
def master():
    master = source.master()
    cr.create('master', 'f2s/fuel_node',
              {'index': master[0], 'ip': master[1]})


def dep_name(dep):
    if dep.get('node_id', None) is None:
        return dep['name']
    else:
        return '{}_{}'.format(dep['name'], dep['node_id'])


def create(*args, **kwargs):
    try:
        return resource.Resource(*args, **kwargs)
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
    node_res = resource.load('node%s' % node) if node != 'null' else None
    for task in graph[node]:
        meta = directory[task['id']]
        if node == 'null':
            name = task['id']
        else:
            name = '{}_{}'.format(task['id'], node)
        if task['type'] == 'skipped':
            res = create(name, 'f2s/noop')
        elif task['type'] == 'shell':
            res = create(name, 'f2s/command', {
                'cmd': meta['parameters']['cmd'],
                'timeout': meta['parameters'].get('timeout', 30)})
        elif task['type'] == 'sync':
            res = create(name, 'f2s/sync',
                         {'src': meta['parameters']['src'],
                          'dst': meta['parameters']['dst']})
        elif task['type'] == 'copy_files':
            res = create(name, 'resources/sources',
                         {'sources': meta['parameters']['files']})
        elif task['type'] == 'puppet':
            res = create(name, 'f2s/' + task['id'])
        elif task['type'] == 'upload_file':
            # upload nodes info is not handled yet
            pass
        else:
            raise Exception('Unknown task type %s' % task)
        if node_res and res:
            node_res.connect(res)
        for dep in task.get('requires', []):
            dg.add_edge(dep_name(dep), name)
        for dep in task.get('required_for', []):
            dg.add_edge(name, dep_name(dep))
    for u, v in dg.edges():
        if u == v:
            continue
        try:
            if node == 'null':
                evapi.add_react(u, v, actions=('run', 'update'))
            else:
                evapi.add_dep(u, v, actions=('run', 'update'))
        except Exception as exc:
            print exc


@main.command()
@click.argument('env_id', type=click.INT)
@click.argument('uids', nargs=-1)
def prep(env_id, uids):
    for uid in uids:
        node = resource.load('node{}'.format(uid))
        res = resource.Resource('fuel_data{}'.format(uid), 'f2s/fuel_data',
                                {'uid': uid, 'env': env_id})
        evapi.add_react(res.name, 'pre_deployment_start',
                        actions=('run', 'update'))
        node = resource.load('node{}'.format(uid))
        node.connect(res, {})


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
