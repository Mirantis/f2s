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


class NailgunSource(object):

    def nodes(self, uids):
        from fuelclient.objects.node import Node
        return map(Node, uids)

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


def create_master():
    master = source.master()
    try:
        cr.create('master', 'f2s/fuel_node',
                  {'index': master[0], 'ip': master[1]})
    except Exception as exc:
        print exc


source = NailgunSource()


def node(nobj):
    cr.create('fuel_node', 'f2s/fuel_node',
              {'index': nobj.data['id'], 'ip': nobj.data['ip']})


def fuel_data(nobj):
    uid = str(nobj.data['id'])
    env_id = nobj.data['cluster']
    node = resource.load('node{}'.format(uid))
    res = resource.Resource('fuel_data{}'.format(uid), 'f2s/fuel_data',
                            {'uid': uid,
                             'env': env_id})
    evapi.add_react(res.name, 'pre_deployment_start',
                    actions=('run', 'update'))
    node = resource.load('node{}'.format(uid))
    node.connect(res, {})


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


def allocate(nailgun_graph, node):
    dg = nx.DiGraph()
    graph = nailgun_graph['tasks_graph']
    directory = nailgun_graph['tasks_directory']
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
            res = create(name, 'f2s/content', meta['parameters'])
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


def _prefetch(env, uids):
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


@click.group()
def main():
    pass


@main.command()
def master():
    create_master()


@main.command()
@click.argument('env_id')
def init(env_id):
    """Prepares anchors and master tasks for environment,
    create master resource if it doesnt exist
    """
    graph = source.graph(env_id)
    create_master()
    for name in ['null', 'master']:
        allocate(graph, name)


@main.command()
@click.argument('uids', nargs=-1)
def nodes(uids):
    """Creates nodes with transports and fuel_data resource for each node
    """
    for nobj in source.nodes(uids):
        node(nobj)
        fuel_data(nobj)


@main.command()
@click.argument('env_id')
@click.argument('uids', nargs=-1)
def assign(env_id, uids):
    """Assign resources to nodes based on fuel tasks
    """
    graph = source.graph(env_id)
    for uid in uids:
        allocate(graph, uid)


@main.command()
@click.argument('env_id', type=click.INT)
@click.argument('uids', nargs=-1)
def prefetch(env_id, uids):
    """Update fuel data with most recent data
    """
    _prefetch(Environment(env_id), uids)


@main.command()
@click.argument('env_id')
@click.argument('uids', nargs=-1)
def env(env_id, uids):
    """Prepares solar environment based on fuel environment.
    It should perform all required changes for solar to work
    """
    env = Environment(env_id)
    uids = uids or [str(n.data['id']) for n in env.get_all_nodes()]
    for nobj in source.nodes(uids):
        node(nobj)
        fuel_data(nobj)
    _prefetch(env, uids)
    create_master()
    graph = source.graph(env_id)
    for name in ['null', 'master'] + uids:
        allocate(graph, name)


if __name__ == '__main__':
    main()
    ModelMeta.session_end()
