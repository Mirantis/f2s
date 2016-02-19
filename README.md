#How to install on fuel master?

First add repository for zeromq:
```
cat << EOF > /etc/yum.repos.d/zmq.repo
[home_fengshuo_zeromq]
name=The latest stable of zeromq builds (CentOS_CentOS-6)
type=rpm-md
baseurl=http://download.opensuse.org/repositories/home:/fengshuo:/zeromq/CentOS_CentOS-6/
gpgcheck=1
gpgkey=http://download.opensuse.org/repositories/home:/fengshuo:/zeromq/CentOS_CentOS-6/repodata/repomd.xml.key
enabled=1
EOF
```
Then install solar:
```
pip install solar
```

#f2s.py

This script converts tasks.yaml + library actions into solar resources,
vrs, and events.

1. Based on tasks.yaml meta.yaml is generated, you can take a look on example
at f2s/resources/netconfig/meta.yaml
2. Based on hiera lookup we generated inputs for each resource, patches can be
found at f2s/patches
3. VRs (f2s/vrs) generated based on dependencies between tasks and roles

#fsclient.py

This script helps to create solar resource with some of nailgun data.
Note, you should run it inside of the solar container.

`./f2s/fsclient.py master 1`
Accepts cluster id, prepares transports for master + generate keys task
for current cluster.

`./f2s/fsclient.py nodes 1`
Prepares transports for provided nodes, ip and cluster id fetchd from nailgun.

`./f2s/fsclient.py prep 1`
Creates tasks for syncing keys + fuel-library modules.

`./f2s/fsclient.py roles 1`
Based on roles stored in nailgun we will assign vrs/<role>.yaml to a given
node. Right now it takes while, so be patient.

#fetching data from nailgun

Special entity added which allows to fetch data from any source
*before* any actual deployment.
This entity provides mechanism to specify *manager* for resource (or list of them).
Manager accepts inputs as json in stdin, and outputs result in stdout,
with result of manager execution we will update solar storage.

Examples can be found at f2s/resources/role_data/managers.

Data will be fetched on solar command

`solar res prefetch -n <resource name>`

#tweaks

Several things needs to be manually adjusted before you can use solar
on fuel master.

- provision a node by fuel
  `fuel node --node 1 --provision`
- create /var/lib/astute directory on remote
- install repos using fuel
  `fuel node --node 1 --tasks core_repos`
- configure hiera on remote, and create /etc/puppet/hieradata directory
```
 :backends:
  - yaml
  #- json
:yaml:
  :datadir: /etc/puppet/hieradata
:json:
  :datadir: /etc/puppet/hieradata
:hierarchy:
  - "%{resource_name}"
  - resource
```

All of this things will be automated by solar eventually

#basic troubleshooting

If there are any Fuel plugin installed, you should manually
create a stanza for it in the `./f2s/resources/role_data/meta.yaml`,
like:
```
input:
  foo_plugin_name:
    value: null
```

And regenerate the data from nailgun,

To regenerate the deployment data to Solar resources make
```
solar res clear_all
```

and repeat all of the fsclient.py and fetching nailgun data steps
