#How to install on fuel master?

If solar package is not available yet install solar from pip:

```
yum install gcc-c++
pip install virtualenv
virtualenv venv
source venv/bin/activate

pip install solar
```

# configure solar:
All of this things will be automated by solar eventually

```
mkdir /etc/solar
echo solar_db: sqlite:////tmp/solar.db > /etc/solar/solar.conf

# generate solar resources from library tasks
f2s t2r --dir /tmp/f2s --lib /etc/puppet/modules

# create solar Resource defintions repository
mkdir -p /var/lib/solar/repositories
solar repo import /tmp/f2s -n f2s
solar repo update f2s f2s/created

# clone solar-resources and create repositories
git clone https://github.com/openstack/solar-resources.git
solar repo import solar-resources/resources
```

before doing any deployment using solar - provision nodes
```
# provision a node by fuel
fuel node --node 1 --provision
```

workflow
```
# prepare all configuration in fuel and then use fsclient to copy fuel env
# with all configured nodes, -f flag adds master and null preudo role
fsclient env 1 -f
# if u want to copy only specific list of nodes - use
fsclient env 1 1 2 3

# switc to usual solar workflow
solar ch stage
solar ch process
solar or run-once last
```

Handling updates

1. adding a new node
```
# environment 1 with node 4 was created
fsclient env 1 4 -f
# then node with uid 3 needs to be added, but running next command wont be enough
fsclient env 1 3
# it will create only new node, without updating edges for 4th node that points to
# 3rd, correct way to handle it will be
fsclient env 1 3 4
```

2. adding a role to a deployed node (any partial changes of fuel graph)
```
# fetch graph and make sure that fuel_data is changed, on change of
# fuel_data - stages will be inserted in the graph
fsclient env 1 4
```
Updating partially graph has some caveats - sometime nailgun may rely
on skipped tasks to preserve correct order of execution, however if some of
such tasks were commited with 1st role - solar may generate incorrect graph
from nailgun point of view. For now it is not clear how to address this issue.
For an example of such issue - create controller, commit it with solar, and then
add cinder to the same node. You will see that tasks ntp-client and top-role-cinder
can be run in parallel, which should be the case - they are in different
stages.
