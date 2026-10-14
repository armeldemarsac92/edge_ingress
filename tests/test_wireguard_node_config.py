import base64
import pathlib
import re
import unittest
import jinja2
import yaml

ROOT=pathlib.Path(__file__).resolve().parents[1]
ROLE=ROOT/'roles/edge_wireguard'

class NodeWireGuardConfigTest(unittest.TestCase):
    def setUp(self):
        tasks=yaml.safe_load((ROLE/'tasks/node.yml').read_text())
        self.task=next(x for x in tasks if 'ansible.builtin.template' in x)
        self.env=jinja2.Environment(loader=jinja2.FileSystemLoader(ROLE/'templates'),undefined=jinja2.StrictUndefined)
        self.env.filters['b64decode']=lambda x:base64.b64decode(x).decode()
        self.env.filters['regex_replace']=lambda text,pattern,replacement:re.sub(pattern,replacement,text)
        self.context={
            'edge_wireguard_node_link':{'node_if':'wg-vps','node_addr':'10.77.11.2/30','vps_addr':'10.77.11.1/30','vps_if':'wg-os1','vps_listen_port':51811},
            'edge_wireguard_generated_private_keys':{'wg-vps':'fixture-private-key'},
            'edge_wireguard_vps_inventory_name':'edge1',
            'hostvars':{'edge1':{'edge_wireguard_generated_public_keys':{'wg-os1':'fixture-public-key'},'edge_vps_loopback_addr':'10.77.255.1/32','edge_vps_public_endpoint':'203.0.113.10'}},
            'edge_vps_loopback_addr':'10.77.255.1/32',
            'edge_wireguard_node_persistent_keepalive':25,'edge_wireguard_mtu':1380,
            'edge_wireguard_previous_config':{},
        }

    def rendered(self, override=None):
        if override is not None:self.context['edge_wireguard_node_link']['node_listen_port']=override
        for name,expression in self.task['vars'].items():
            self.context[name]=self.env.from_string(expression).render(self.context)
        return self.env.get_template('wg-quick.conf.j2').render(self.context)

    def handler(self, before):
        after=self.rendered()
        if before is not None:self.context['edge_wireguard_previous_config']={'content':base64.b64encode(before.encode()).decode()}
        self.context['lookup']=lambda name,path:self.env.get_template(path).render(self.context)
        notification=self.task['notify']
        self.assertIsInstance(notification,str)
        return self.env.from_string(notification).render(self.context).strip(),after

    def test_port_validation_rejects_fractional_and_out_of_range_values(self):
        tasks=yaml.safe_load((ROLE/'tasks/validate.yml').read_text())
        rules=next(x['ansible.builtin.assert']['that'] for x in tasks if x['name']=='Validate OpenStack WireGuard variables')
        self.env.tests['match']=lambda value,pattern:bool(re.match(pattern,value))
        context={'edge_wireguard_node_name':'node1','groups':{'edge_vps':['edge1']},'wireguard_links':{'node1':self.context['edge_wireguard_node_link']}}
        for value in ['51811.5',51811.5,0,65536,-1,'']:
            with self.subTest(value=value):
                context['wireguard_links']['node1']['node_listen_port']=value
                self.assertFalse(all(self.env.compile_expression(rule)(context) for rule in rules))

    def test_default_port_is_stable(self):
        self.assertIn('ListenPort = 51811',self.rendered())

    def test_explicit_port_override_is_preserved(self):
        self.assertIn('ListenPort = 48000',self.rendered(48000))

    def test_adding_saved_port_reloads_without_stopping_dependencies(self):
        current=self.rendered().replace('ListenPort = 51811\n','')
        self.assertEqual(self.handler(current)[0],'Reload WireGuard interfaces')

    def test_changing_only_port_reloads(self):
        current=self.rendered().replace('ListenPort = 51811','ListenPort = 39402')
        self.assertEqual(self.handler(current)[0],'Reload WireGuard interfaces')

    def test_mtu_change_retains_restart_behavior(self):
        current=self.rendered().replace('MTU = 1380','MTU = 1420')
        self.assertEqual(self.handler(current)[0],'Restart WireGuard interfaces')

    def test_address_change_retains_restart_behavior(self):
        current=self.rendered().replace('Address = 10.77.11.2/30','Address = 10.77.19.2/30')
        self.assertEqual(self.handler(current)[0],'Restart WireGuard interfaces')

    def test_new_interface_uses_start_restart_path(self):
        self.assertEqual(self.handler(None)[0],'Restart WireGuard interfaces')

if __name__=='__main__':unittest.main()
