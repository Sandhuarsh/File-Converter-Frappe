from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [line.strip() for line in f if line.strip()]

setup(
    name="file_converter",
    version="0.0.1",
    description="Drag-and-drop file conversion on every ERPNext form",
    author="Innosphere",
    author_email="dev@example.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
