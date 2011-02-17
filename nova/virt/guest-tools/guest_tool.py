# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack LLC.
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
import sys
import subprocess
import time
import array
import struct
import socket
import platform
import logging

FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
if sys.platform == 'win32':
    LOG_DIR = os.path.join(os.environ.get('ALLUSERSPROFILE'), 'openstack')
elif sys.platform == 'linux2':
    LOG_DIR = '/var/log/openstack'
else:
    LOG_DIR = 'logs'
if not os.path.exists(LOG_DIR):
    os.mkdir(LOG_DIR)
LOG_FILENAME = os.path.join(LOG_DIR, 'openstack-guest-tools.log')
logging.basicConfig(filename=LOG_FILENAME, format=FORMAT)

if sys.hexversion < 0x3000000:
    _byte = ord    # 2.x chr to integer
else:
    _byte = int     # 3.x byte to integer


class ProcessExecutionError:
    """
    Process Execution Error Class
    """

    def __init__(self, exit_code, stdout, stderr, cmd):
        """
        The Intializer
        """
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.cmd = cmd

    def __str__(self):
        """
        The informal string representation of the object
        """
        return str(self.exit_code)


def _bytes2int(bytes):
    """
    convert bytes to int.
    """
    intgr = 0
    for byt in bytes:
        intgr = (intgr << 8) + _byte(byt)
    return intgr


def _parse_network_details(machine_id):
    """
    Parse the machine.id field to get MAC, IP, Netmask and Gateway feilds
    machine.id is of the form MAC;IP;Netmask;Gateway;
    ; is the separator
    """
    network_details = []
    if machine_id[1].strip() == 'No machine id':
        pass
    else:
        network_info_list = machine_id[0].split(';')
        assert len(network_info_list) % 4 == 0
        for i in xrange(0, len(network_info_list) / 4):
            network_details.append((network_info_list[i].strip().lower(),
                                    network_info_list[i + 1].strip(),
                                    network_info_list[i + 2].strip(),
                                    network_info_list[i + 3].strip()))
    return network_details


def _get_windows_network_adapters():
    """
    Get the list of windows network adapters
    """
    import win32com.client
    wbem_locator = win32com.client.Dispatch('WbemScripting.SWbemLocator')
    wbem_service = wbem_locator.ConnectServer('.', 'root\cimv2')
    wbem_network_adapters = wbem_service.InstancesOf('Win32_NetworkAdapter')
    network_adapters = []
    for wbem_network_adapter in wbem_network_adapters:
        if wbem_network_adapter.NetConnectionStatus == 2 or \
                                wbem_network_adapter.NetConnectionStatus == 7:
            adapter_name = wbem_network_adapter.NetConnectionID
            mac_address = wbem_network_adapter.MacAddress.lower()
            wbem_network_adapter_config = \
                wbem_network_adapter.associators_(
                          'Win32_NetworkAdapterSetting',
                          'Win32_NetworkAdapterConfiguration')[0]
            ip_address = ''
            subnet_mask = ''
            if wbem_network_adapter_config.IPEnabled:
                ip_address = wbem_network_adapter_config.IPAddress[0]
                subnet_mask = wbem_network_adapter_config.IPSubnet[0]
                #wbem_network_adapter_config.DefaultIPGateway[0]
            network_adapters.append({'name': adapter_name,
                                    'mac-address': mac_address,
                                    'ip-address': ip_address,
                                    'subnet-mask': subnet_mask})
    return network_adapters


def _get_linux_network_adapters():
    """
    Get the list of Linux network adapters
    """
    import fcntl
    max_bytes = 8096
    arch = platform.architecture()[0]
    if arch == '32bit':
        offset1 = 32
        offset2 = 32
    elif arch == '64bit':
        offset1 = 16
        offset2 = 40
    else:
        raise OSError("Unknown architecture: %s" % arch)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    names = array.array('B', '\0' * max_bytes)
    outbytes = struct.unpack('iL', fcntl.ioctl(
        sock.fileno(),
        0x8912,
        struct.pack('iL', max_bytes, names.buffer_info()[0])))[0]
    adapter_names = \
        [names.tostring()[n_counter:n_counter + offset1].split('\0', 1)[0]
        for n_counter in xrange(0, outbytes, offset2)]
    network_adapters = []
    for adapter_name in adapter_names:
        ip_address = socket.inet_ntoa(fcntl.ioctl(
            sock.fileno(),
            0x8915,
            struct.pack('256s', adapter_name))[20:24])
        subnet_mask = socket.inet_ntoa(fcntl.ioctl(
            sock.fileno(),
            0x891b,
            struct.pack('256s', adapter_name))[20:24])
        raw_mac_address = '%012x' % _bytes2int(fcntl.ioctl(
            sock.fileno(),
            0x8927,
            struct.pack('256s', adapter_name))[18:24])
        mac_address = ":".join([raw_mac_address[m_counter:m_counter + 2]
            for m_counter in range(0, len(raw_mac_address), 2)]).lower()
        network_adapters.append({'name': adapter_name,
                                 'mac-address': mac_address,
                                 'ip-address': ip_address,
                                 'subnet-mask': subnet_mask})
    return network_adapters


def _get_adapter_name_and_ip_address(network_adapters, mac_address):
    """
    Get the adapter name based on the MAC address
    """
    adapter_name = None
    ip_address = None
    for network_adapter in network_adapters:
        if network_adapter['mac-address'] == mac_address.lower():
            adapter_name = network_adapter['name']
            ip_address = network_adapter['ip-address']
            break
    return adapter_name, ip_address


def _get_win_adapter_name_and_ip_address(mac_address):
    """
    Get the Windows network adapter name
    """
    network_adapters = _get_windows_network_adapters()
    return _get_adapter_name_and_ip_address(network_adapters, mac_address)


def _get_linux_adapter_name_and_ip_address(mac_address):
    """
    Get the Linux adapter name
    """
    network_adapters = _get_linux_network_adapters()
    return _get_adapter_name_and_ip_address(network_adapters, mac_address)


def _execute(cmd_list, process_input=None, check_exit_code=True):
    """
    Executes the command with the list of arguments specified
    """
    cmd = ' '.join(cmd_list)
    logging.debug('Executing command "%s"' % cmd)
    env = os.environ.copy()
    obj = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    result = None
    if process_input != None:
        result = obj.communicate(process_input)
    else:
        result = obj.communicate()
    obj.stdin.close()
    if obj.returncode:
        logging.debug('Result was %s' % obj.returncode)
        if check_exit_code and obj.returncode != 0:
            (stdout, stderr) = result
            raise ProcessExecutionError(exit_code=obj.returncode,
                                        stdout=stdout,
                                        stderr=stderr,
                                        cmd=cmd)
    time.sleep(0.1)
    return result


def _windows_set_ipaddress():
    """
    Set IP address for the windows VM
    """
    program_files = os.environ.get('PROGRAMFILES')
    program_files_x86 = os.environ.get('PROGRAMFILES(X86)')
    vmware_tools_bin = None
    if os.path.exists(os.path.join(program_files, 'VMware', 'VMware Tools',
                                   'vmtoolsd.exe')):
        vmware_tools_bin = os.path.join(program_files, 'VMware',
                                    'VMware Tools', 'vmtoolsd.exe')
    elif os.path.exists(os.path.join(program_files, 'VMware', 'VMware Tools',
                                     'VMwareService.exe')):
        vmware_tools_bin = os.path.join(program_files, 'VMware',
                                      'VMware Tools', 'VMwareService.exe')
    elif program_files_x86 and os.path.exists(os.path.join(program_files_x86,
                                       'VMware', 'VMware Tools',
                                       'VMwareService.exe')):
        vmware_tools_bin = os.path.join(program_files_x86, 'VMware',
                                        'VMware Tools', 'VMwareService.exe')
    if vmware_tools_bin:
        cmd = ['"' + vmware_tools_bin + '"', '--cmd', 'machine.id.get']
        for network_detail in _parse_network_details(_execute(cmd,
                                              check_exit_code=False)):
            mac_address, ip_address, subnet_mask, gateway = network_detail
            adapter_name, current_ip_address = \
                    _get_win_adapter_name_and_ip_address(mac_address)
            if adapter_name and not ip_address == current_ip_address:
                cmd = ['netsh', 'interface', 'ip', 'set', 'address',
                       'name="%s"' % adapter_name, 'source=static', ip_address,
                       subnet_mask, gateway, '1']
                _execute(cmd)
    else:
        logging.warn('VMware Tools is not installed')


def _linux_set_ipaddress():
    """
    Set IP address for the Linux VM
    """
    vmware_tools_bin = None
    if os.path.exists('/usr/sbin/vmtoolsd'):
        vmware_tools_bin = '/usr/sbin/vmtoolsd'
    elif os.path.exists('/usr/bin/vmtoolsd'):
        vmware_tools_bin = '/usr/bin/vmtoolsd'
    elif os.path.exists('/usr/sbin/vmware-guestd'):
        vmware_tools_bin = '/usr/sbin/vmware-guestd'
    elif os.path.exists('/usr/bin/vmware-guestd'):
        vmware_tools_bin = '/usr/bin/vmware-guestd'
    if vmware_tools_bin:
        cmd = [vmware_tools_bin, '--cmd', 'machine.id.get']
        for network_detail in _parse_network_details(_execute(cmd,
                                              check_exit_code=False)):
            mac_address, ip_address, subnet_mask, gateway = network_detail
            adapter_name, current_ip_address = \
                    _get_linux_adapter_name_and_ip_address(mac_address)
            if adapter_name and not ip_address == current_ip_address:
                interface_file_name = \
                    '/etc/sysconfig/network-scripts/ifcfg-%s' % adapter_name
                #Remove file
                os.remove(interface_file_name)
                #Touch file
                _execute(['touch', interface_file_name])
                interface_file = open(interface_file_name, 'w')
                interface_file.write('\nDEVICE=%s' % adapter_name)
                interface_file.write('\nUSERCTL=yes')
                interface_file.write('\nONBOOT=yes')
                interface_file.write('\nBOOTPROTO=static')
                interface_file.write('\nBROADCAST=')
                interface_file.write('\nNETWORK=')
                interface_file.write('\nNETMASK=%s' % subnet_mask)
                interface_file.write('\nIPADDR=%s' % ip_address)
                interface_file.write('\nMACADDR=%s' % mac_address)
                interface_file.close()
        _execute(['/sbin/service', 'network' 'restart'])
    else:
        logging.warn('VMware Tools is not installed')

if __name__ == '__main__':
    pltfrm = sys.platform
    if pltfrm == 'win32':
        _windows_set_ipaddress()
    elif pltfrm == 'linux2':
        _linux_set_ipaddress()
    else:
        raise NotImplementedError('Platform not implemented:"%s"' % pltfrm)
