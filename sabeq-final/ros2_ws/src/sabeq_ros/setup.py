import os
from glob import glob

from setuptools import find_packages, setup


package_name = 'sabeq_ros'


setup(
    name=package_name,
    version='0.0.0',

    packages=find_packages(exclude=['test']),

    data_files=[
        (
            'share/ament_index/resource_index/packages',
            ['resource/' + package_name],
        ),
        (
            'share/' + package_name,
            ['package.xml'],
        ),
        (
            os.path.join('share', package_name, 'config'),
            glob('config/*.yaml'),
        ),
    ],

    install_requires=['setuptools'],

    zip_safe=True,

    maintainer='abdulaziz',

    maintainer_email='abdulaziz_6a24@yahoo.com',

    description='TODO: Package description',

    license='TODO: License declaration',

    extras_require={
        'test': [
            'pytest',
        ],
    },

    entry_points={
        'console_scripts': [
    'yolo_node = sabeq_ros.yolo_node:main',
    'autonomy_node = sabeq_ros.autonomy_node:main',
    'mission_node = sabeq_ros.mission_node:main',
    'cart_node = sabeq_ros.cart_node:main',
],
    },
)