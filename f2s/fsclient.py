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
import solar
from solar.core.resource import composer as cr
from solar.core import resource
from solar.dblayer.model import ModelMeta
from solar.events import api as evapi
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
        resource.load('nodemaster')
    except solar.dblayer.model.DBLayerNotFound:
        cr.create('master', 'f2s/fuel_node',
                  {'index': master[0], 'ip': master[1]})


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
    events = [
        evapi.React(res.name, 'run', 'success',
                    'pre_deployment_start', 'run'),
        evapi.React(res.name, 'update', 'success',
                    'pre_deployment_start', 'run')]
    evapi.add_events(res.name, events)
    node = resource.load('node{}'.format(uid))
    node.connect(res, {})


def dep_name(dep):
    if dep.get('node_id', None) is None:
        return dep['name']
    else:
        return '{}_{}'.format(dep['name'], dep['node_id'])


def create(*args, **kwargs):
    try:
        return resource.load(args[0])
    except Exception as exc:
        return resource.Resource(*args, **kwargs)


def create_from_task(task, meta, node, node_res):
    if node == 'null':
        name = task['id']
        node = None
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
        yield dep['node_id'], dep['name'], node, task['id']
    for dep in task.get('required_for', []):
        yield node, task['id'], dep['node_id'], dep['name']


def create_from_graph(graph, directory, node):
    node_res = resource.load('node%s' % node) if node != 'null' else None
    for task in graph[node]:
        meta = directory[task['id']]
        for edge in create_from_task(task, meta, node, node_res):
            yield edge


def name_from(node, task_name):
    if node:
        return task_name + '_' + node
    return task_name


def allocate(nailgun_graph, uids):
    edges = []
    graph = nailgun_graph['tasks_graph']
    directory = nailgun_graph['tasks_directory']
    for uid in uids:
        for edge in create_from_graph(graph, directory, uid):
            edges.append(edge)
    for node_u, u, node_v, v in edges:
        if (node_u, u) == (node_v, v):
            continue
        name_u = name_from(node_u, u)
        name_v = name_from(node_v, v)
        try:
            # anchors of deployment should be always present in graph
            if node_u is None and node_v is None:
                evapi.add_react(name_u, name_v, actions=('run', 'update'))
            else:
                evapi.add_dep(name_u, name_v, actions=('run', 'update'))
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
    create_master()
    allocate(source.graph(env_id), ['null', 'master'])


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
    allocate(graph, uids)


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
@click.option('-f', '--full', is_flag=True)
def env(env_id, uids, full):
    """Prepares solar environment based on fuel environment.
    It should perform all required changes for solar to work
    """
    env = Environment(env_id)
    uids = list(uids) if uids else [
        str(n.data['id']) for n in env.get_all_nodes()]
    for nobj in source.nodes(uids):
        node(nobj)
        fuel_data(nobj)
    _prefetch(env, uids)
    create_master()
    allocate(
        source.graph(env_id),
        ['null', 'master'] + uids if full else uids)


if __name__ == '__main__':
    main()
    ModelMeta.session_end()
