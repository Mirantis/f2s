#!/usr/bin/env python

import os

import click
from solar.core.resource import composer as cr
from solar.dblayer.model import ModelMeta



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
        cr.create('fuel_node', 'vrs/fuel_node',
            {'index': uid, 'ip': ip})

@main.command()
@click.argument('env')
def master(env):
    master = source.master()
    cr.create('master', 'vrs/fuel_node',
        {'index': master[0], 'ip': master[1]})

    cr.create('genkeys', 'vrs/genkeys', {
        'node': 'node'+master[0],
        'index': int(env)})

@main.command()
@click.argument('uids', nargs=-1)
def prep(uids):
    for uid, ip, env in source.nodes(uids):
        cr.create('prep', 'vrs/prep',
            {'index': uid, 'env': env, 'node': 'node'+str(uid)})


@main.command()
@click.argument('uids', nargs=-1)
def roles(uids):

    for uid, ip, env in source.nodes(uids):
        for role in source.roles(uid):
            cr.create(role, 'vrs/'+role,
                {'index': uid, 'env': env, 'node': 'node'+str(uid)})


if __name__ == '__main__':
    main()
    ModelMeta.session_end()
