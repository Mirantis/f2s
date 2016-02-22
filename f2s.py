#!/usr/bin/env python

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
CONDITONAL = os.path.join(VR_TMP_DIR, 'conditional')
ensure_dir(VR_TMP_DIR)
ensure_dir(CONDITIONAL)
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

    def meta(self):
        if self.data['type'] == 'skipped':
            data = OrderedDict([('id', self.name),
                ('handler', 'none'),
                ('version', '8.0'),
                ('inputs', {})])
        elif self.data['type'] == 'puppet':
            man_path = self.data['parameters']['puppet_manifest']
            data = OrderedDict([('id', self.name),
                    ('handler', 'puppetv2'),
                    ('version', '8.0'),
                    ('actions', {
                        'run': man_path,
                        'update': man_path}),
                    ('input', {}),])
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

    def inputs(self):
        """
        Inputs prepared by

        fuel_noop_tests.rb
        identity = spec.split('/')[-1]
        ENV["SPEC"] = identity

        hiera.rb
        File.open("/tmp/fuel_specs/#{ENV['SPEC']}", 'a') { |f| f << "- #{key}\n" }
        """
        print self.spec_name
        lookup_stack_path = os.path.join(
            INPUTS_LOCATION, self.spec_name)
        if not os.path.exists(lookup_stack_path):
            return {}

        with open(lookup_stack_path) as f:
            data = yaml.safe_load(f) or []
        data = data + ['puppet_modules']
        return {key: {'value': None} for key
                in set(data) if '::' not in key}


class DGroup(object):

    filtered = ['hiera', 'deploy_start']

    def __init__(self, name, tasks):
        self.name = name
        self.tasks = tasks

    def resources(self):

        for t, _, _ in self.tasks:
            if t.name in self.filtered:
                continue
            yield OrderedDict(
                [('id', t.name),
                 ('from', 'f2s/resources/' + t.name),
                 ('location', "{{node}}")])

    def events(self):
        for t, inner, outer in self.tasks:
            if t.name in self.filtered:
                continue

            for dep in set(inner):
                if dep in self.filtered:
                    continue

                yield OrderedDict([
                    ('type', 'depends_on'),
                    ('state', 'success'),
                    ('parent', {
                        'with_tags': ['resource=' + dep, 'node={{index}}'],
                        'action': 'run'}),
                    ('depend_action', t.name + '{{index}}.run')])
            for dep in set(outer):
                if dep in self.filtered:
                    continue

                yield OrderedDict([
                    ('type', 'depends_on'),
                    ('state', 'success'),
                    ('parent', {
                        'with_tags': ['resource=' + dep],
                        'action': 'run'}),
                    ('depend_action', t.name + '{{index}}.run')])

    def meta(self):
        data = OrderedDict([
            ('id', self.name),
            ('resources', list(self.resources())),
            ('events', list(self.events()))])
        return ordered_dump(data, default_flow_style=False)

    @property
    def path(self):
        return os.path.join(VR_TMP_DIR, self.name + '.yml')


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
             ('events', self.events)])

    @property
    def resource(self):
        return OrderedDict(
                [('id', self.task.name),
                 ('from', 'fuel/resources/' + self.taks.name),
                 ('location', "{{node}}")])

    def event(self, tags, child):
        return OrderedDict([
                    ('type', 'depends_on'),
                    ('state', 'success'),
                    ('parent', {
                        'with_tags': tags,
                        'action': 'run'}),
                    ('depend_action', child + '{{index}}.run')])

    @property
    def events(self):
        tags = ['resource=' + self.task.name]
        for node in self.succ:
            yield self.event(
                ['resource=' + self.task.name, 'node={{node}}'], node)
        for node in self.pred:
            yield self.event(
                ['resource=' + node, 'node={{node}}'], self.task.name)

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
    def relative_path(self):
        return 'fuel/vrs/singles/' + self.task.name


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
             ('resources': [{'from': s.relative_path}
                            for s in self.collection]),
             ('tags', self.grouping + '=' + self.name)])

    @property
    def relative_path(self):
        return 'fuel/vrs'


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
@click.argument('groups', nargs=-1)
@click.option('-c', is_flag=True)
def g2vr(groups, c):
    if c:
        clean_vr()

    collection_compositions = {}
    single_compositions = {}
    dg = get_graph()
    for node in nx.topological_sort(dg):
        if 't' in dg.node[node] and dg.node[node]['t'].type in VALID_TASKS:
            pass
        else:
            continue

        task = dg.node[node]['t']
        if task.name not in single_compositions:
            single_compositions[task.name] = SingleTaskComposition(task)
            single_compositions[task.name].pred.update(
                dg.predecessors(node))
            single_compositions[task.name].pred_cross.update(
                task.cross_node)





if __name__ == '__main__':
    main()
