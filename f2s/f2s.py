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
from fnmatch import fnmatch
import shutil
from collections import OrderedDict

import click
import yaml
import networkx as nx


def ensure_dir(dir):
    try:
        os.makedirs(dir)
    except OSError:
        pass

CURDIR = os.path.dirname(os.path.realpath(__file__))

LIBRARY_PATH = os.path.join('..', 'fuel-library')
RESOURCE_TMP_WORKDIR = os.path.join(CURDIR, 'tmp/resources')
ensure_dir(RESOURCE_TMP_WORKDIR)
RESOURCE_DIR = os.path.join(CURDIR, 'resources')
VR_TMP_DIR = os.path.join(CURDIR, 'tmp/vrs')
ensure_dir(VR_TMP_DIR)
INPUTS_LOCATION = "/root/current/"
DEPLOYMENT_GROUP_PATH = os.path.join(LIBRARY_PATH,
    'deployment', 'puppet', 'deployment_groups', 'tasks.yaml')

VALID_TASKS = ('puppet', 'skipped')


def clean_resources():
    shutil.rmtree(RESOURCE_TMP_WORKDIR)
    ensure_dir(RESOURCE_TMP_WORKDIR)


def clean_vr():
    shutil.rmtree(VR_TMP_DIR)
    ensure_dir(VR_TMP_DIR)


def ordered_dump(data, stream=None, Dumper=yaml.Dumper, **kwds):
    class OrderedDumper(Dumper):
        pass

    def _dict_representer(dumper, data):
        return dumper.represent_mapping(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            data.items())
    OrderedDumper.add_representer(OrderedDict, _dict_representer)
    return yaml.dump(data, stream, OrderedDumper, **kwds)


class Task(object):

    def __init__(self, task_data, task_path):
        self.data = task_data
        self.src_path = task_path
        self.name = self.data['id']
        self.type = self.data['type']
        self.grouping = self.data.get('roles') or self.data.get('groups', [])

    def edges(self):
        data = self.data
        if 'required_for' in data:
            for req in data['required_for']:
                yield self.name, req
        if 'requires' in data:
            for req in data['requires']:
                yield req, self.name

        if 'groups' in data:
            for req in data['groups']:
                yield self.name, req
        if 'tasks' in data:
            for req in data['tasks']:
                yield req, self.name

    @property
    def cross_node(self):
        for dep in self.data.get('cross-depends', ()):
            yield dep['name']

    def is_conditional(self):
        return bool(self.data.get('condition'))

    @property
    def manifest(self):
        if self.data['type'] != 'puppet':
            return None
        after_naily = self.data['parameters']['puppet_manifest'].split('osnailyfacter/')[-1]
        return os.path.join(
            LIBRARY_PATH, 'deployment', 'puppet', 'osnailyfacter',
            after_naily)

    @property
    def spec_name(self):
        splitted = self.data['parameters']['puppet_manifest'].split('/')
        directory = splitted[-2]
        name = splitted[-1].split('.')[0]
        return "{}_{}_spec.rb'".format(directory, name)

    @property
    def dst_path(self):
        return os.path.join(RESOURCE_TMP_WORKDIR, self.name)

    @property
    def actions_path(self):
        return os.path.join(self.dst_path, 'actions')

    @property
    def meta_path(self):
        return os.path.join(self.dst_path, 'meta.yaml')

    @property
    def relative_path(self):
        return 'f2s/' + self.name

    def meta(self):
        if self.data['type'] == 'skipped':
            data = OrderedDict([
                ('id', self.name),
                ('handler', 'none'),
                ('version', '8.0'),
                ('inputs', {})])
        elif self.data['type'] == 'puppet':
            man_path = self.data['parameters']['puppet_manifest']
            data = OrderedDict([
                ('id', self.name),
                ('handler', 'puppetv2'),
                ('version', '8.0'),
                ('actions', {
                    'run': man_path,
                    'update': man_path}),
                ('input', self.inputs)])
        else:
            raise NotImplemented('Support for %s' % self.data['type'])
        return ordered_dump(data, default_flow_style=False)

    @property
    def actions(self):
        """yield an iterable of src/dst
        """
        if self.manifest is None:
            return
        yield self.manifest, os.path.join(self.actions_path, 'run.pp')

    @property
    def inputs(self):
        return {'puppet_modules':
                {'type': 'str!', 'value': '/etc/puppet/modules'}}


class SingleTaskComposition(object):
    """
    SingleTaskComposition stores all relations to other resources
    """
    def __init__(self, task):
        self.task = task
        self.succ = set()
        self.pred = set()
        self.succ_cross = set()
        self.pred_cross = set()

    def composition(self):
        return OrderedDict(
            [('name', 'composition_' + self.task.name),
             ('resources', [self.resource]),
             ('events', list(self.events))])

    @property
    def resource(self):
        return OrderedDict(
                [('id', self.task.name),
                 ('from', self.task.relative_path),
                 ('location', "#{node}#")])

    def event(self, tags, child):
        return OrderedDict([
                    ('type', 'depends_on'),
                    ('state', 'success'),
                    ('parent', {
                        'with_tags': tags,
                        'action': 'run'}),
                    ('depend_action', child + '#{index}#.run')])

    @property
    def events(self):
        tags = ['resource=' + self.task.name]
        for node in self.succ:
            yield self.event(
                ['resource=' + self.task.name, 'node=#{node}#'], node)
        for node in self.pred:
            yield self.event(
                ['resource=' + node, 'node=#{node}#'], self.task.name)

        for node in self.succ_cross:
            yield self.event(
                ['resource=' + self.task.name], node)

        for node in self.pred_cross:
            yield self.event(
                ['resource=' + node], self.task.name)

    def __hash__(self):
        return hash(self.task.name)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return other.task.name == self.task.name
        elif isinstance(other, basestring):
            return other == self.task.name

    @property
    def store_path(self):
        return os.path.join(VR_TMP_DIR, 'vr_' + self.task.name + '.yaml')

    @property
    def relative_path(self):
        return 'f2s/vr_' + self.task.name


class CollectionComposition(object):
    """
    Represents arbitrary grouping of SinglesTaskCompositions,
    on compilation produces composition file with list of paths to resources
    """

    def __init__(self, name, collection, grouping='composition'):
        self.name = name
        self.collection = collection
        self.grouping = grouping

    def composition(self):
        return OrderedDict(
            [('id', self.name),
             ('resources', [OrderedDict(
                 [('from', s.relative_path),
                  ('input', {'node': '#{node}#', 'index': '#{index}'})])
                            for s in self.collection]),
             ('tags', self.grouping + '=' + self.name)])

    @property
    def store_path(self):
        return os.path.join(VR_TMP_DIR,
                            '{}_{}.yaml'.format(self.grouping, self.name))

    @property
    def relative_path(self):
        return 'f2s/{}_{}'.format(self.grouping, self.name)


def write_composition(composition):
    with open(composition.store_path, 'w') as f:
        f.write(ordered_dump(composition.composition()))


def get_files(base_dir, file_pattern='*tasks.yaml'):
    for root, _dirs, files in os.walk(base_dir):
        for file_name in files:
            if fnmatch(file_name, file_pattern):
                yield root, file_name


def load_data(base, file_name):
    with open(os.path.join(base, file_name)) as f:
        return yaml.load(f)


def preview(task):
    print 'PATH'
    print task.dst_path
    print 'META'
    print task.meta()
    print 'ACTIONS'
    for action in task.actions:
        print 'src=%s dst=%s' % action


def create(task):
    ensure_dir(task.dst_path)
    if task.actions_path:
        ensure_dir(task.actions_path)
        for src, dst in task.actions:
            shutil.copyfile(src, dst)

    with open(task.meta_path, 'w') as f:
        f.write(task.meta())


def get_tasks():
    for base, task_yaml in get_files(LIBRARY_PATH + '/deployment'):
        for item in load_data(base, task_yaml):
            yield Task(item, base)


def get_graph():
    dg = nx.DiGraph()
    for t in get_tasks():
        dg.add_edges_from(list(t.edges()))
        dg.add_node(t.name, t=t)
    return dg


@click.group()
def main():
    pass


@main.command(help='converts tasks into resources')
@click.argument('tasks', nargs=-1)
@click.option('-t', is_flag=True)
@click.option('-p', is_flag=True)
@click.option('-c', is_flag=True)
def t2r(tasks, t, p, c):
    if c:
        clean_resources()

    for task in get_tasks():
        if task.type not in VALID_TASKS:
            continue

        if task.name in tasks or tasks == ():
            if p:
                preview(task)
            else:
                create(task)


@main.command(help='convert groups into templates')
@click.option('-c', is_flag=True)
def g2vr(c):
    if c:
        clean_vr()

    collection = {}
    singles = []
    dg = get_graph()
    for node in nx.topological_sort(dg):
        if 't' not in dg.node[node]:
            continue
        task = dg.node[node]['t']
        if task.type in VALID_TASKS:
            single = SingleTaskComposition(task)
            single.pred.update(dg.predecessors(node))
            single.pred_cross.update(task.cross_node)
            singles.append(single)

        elif task.type == 'group':
            collection[task.name] = CollectionComposition(
                task.name, [], 'role')

    for single in singles:
        for role in single.task.grouping:
            # regexp
            if role in collection:
                collection[role].collection.append(single)

        write_composition(single)

    for coll in collection.values():
        write_composition(coll)

if __name__ == '__main__':
    main()
