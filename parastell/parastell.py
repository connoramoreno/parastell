import argparse
import yaml
from pathlib import Path

import cubit
import src.pystell.read_vmec as read_vmec

from . import log

from . import invessel_build as ivb
from . import magnet_coils as mc
from . import source_mesh as sm
from . import cubit_io as cubit_io


def make_material_block(mat_tag, block_id, vol_id_str):
    """Issue commands to make a material block using Cubit's
    native capabilities.
    
    Arguments:
       mat_tag (str) : name of material block
       block_id (int) : block number
       vol_id_str (str) : space-separated list of volume ids
    """

    cubit.cmd(
        f'create material "{mat_tag}" property_group '
        '"CUBIT-ABAQUS"'
    )
    cubit.cmd(
        f'block {block_id} add volume {vol_id_str}'
    )
    cubit.cmd(
        f'block {block_id} material \'{mat_tag}\''
    )


class Stellarator(object):
    """Parametrically generates a fusion stellarator reactor core model using
    plasma equilibrium data and user-defined parameters. In-vessel component
    geometries are determined by plasma equilibrium VMEC data and a
    user-defined, three-dimensional radial build, in which thickness values for
    each component are supplied in a grid defined by toroidal and poloidal
    angles. Magnets are defined by coil filament point-locus data and a
    user-defined cross-section. Source meshes are defined on plasma equilibrium
    VMEC data and a structured, uniform grid in magnetic flux space.

    Arguments:
        vmec_file (str): path to plasma equilibrium VMEC file.
        logger (object): logger object (optional, defaults to None). If no
            logger is supplied, a default logger will be instantiated.
    """

    def __init__(
        self,
        vmec_file,
        logger=None
    ):
        
        self.logger = logger
        self.vmec_file = vmec_file

        self.invessel_build = None
        self.magnet_set = None
        self.source_mesh = None

    @property
    def vmec_file(self):
        return self._vmec_file
    
    @vmec_file.setter
    def vmec_file(self, file):
        self._vmec_file = file
        self._vmec_obj = read_vmec.VMECData(self._vmec_file)
        if Path(self._vmec_file).suffix != '.nc':
            e = ValueError(
                'Plasma equilibrium VMEC data file input has extension '
                f'\'{Path(self._vmec_file).suffix}\'. File format must be '
                'netCDF (\'.nc\').'
            )
            self._logger.error(e.args[0])
            raise e

    @property
    def logger(self):
        return self._logger
    
    @logger.setter
    def logger(self, logger_object):
        self._logger = logger_object
        if self._logger == None or not self._logger.hasHandlers():
            self._logger = log.init()

    def construct_invessel_build(
        self, toroidal_angles, poloidal_angles, wall_s, radial_build_dict,
        **kwargs
    ):
        """Construct InVesselBuild class object.

        Arguments:
            toroidal_angles (array of float): toroidal angles at which radial
                build is specified. This list should always begin at 0.0 and it
                is advised not to extend beyond one stellarator period. To
                build a geometry that extends beyond one period, make use of
                the 'repeat' parameter [deg].
            poloidal_angles (array of float): poloidal angles at which radial
                build is specified. This array should always span 360 degrees
                [deg].
            wall_s (float): closed flux surface label extrapolation at wall.
            radial_build_dict (dict): dictionary representing the
                three-dimensional radial build of in-vessel components,
                including
                {
                    'component': {
                        'thickness_matrix': 2-D matrix defining component
                            thickness at (toroidal angle, poloidal angle)
                            locations. Rows represent toroidal angles, columns
                            represent poloidal angles, and each must be in the
                            same order provided in toroidal_angles and
                            poloidal_angles [cm](ndarray(float)).
                        'mat_tag': DAGMC material tag for component in DAGMC
                            neutronics model (str, optional, defaults to None).
                            If none is supplied, the 'component' key will be
                            used.
                    }
                }.

        Optional attributes:
            repeat (int): number of times to repeat build segment for full model
                (defaults to 0).
            num_ribs (int): total number of ribs over which to loft for each
                build segment (defaults to 61). Ribs are set at toroidal angles
                interpolated between those specified in 'toroidal_angles' if
                this value is greater than the number of entries in
                'toroidal_angles'.
            num_rib_pts (int): total number of points defining each rib spline
                (defaults to 61). Points are set at poloidal angles interpolated
                between those specified in 'poloidal_angles' if this value is
                greater than the number of entries in 'poloidal_angles'.
            scale (float): a scaling factor between the units of VMEC and [cm]
                (defaults to m2cm = 100).
            plasma_mat_tag (str): alternate DAGMC material tag to use for
                plasma. If none is supplied, 'plasma' will be used (defaults to
                None).
            sol_mat_tag (str): alternate DAGMC material tag to use for
                scrape-off layer. If none is supplied, 'sol' will be used
                (defaults to None).
        """
        self.radial_buid = ivb.RadialBuild(
            toroidal_angles,
            poloidal_angles,
            wall_s,
            radial_build_dict,
            logger=self._logger,
            kwargs=kwargs
        )

        self.invessel_build = ivb.InVesselBuild(
            self._vmec_obj,
            self.radial_buid,
            logger=self._logger,
            **kwargs
        )

        self.invessel_build.populate_surfaces()
        self.invessel_build.calculate_loci()
        self.invessel_build.generate_components()

    def export_invessel_build(
        self, export_cad_to_dagmc=False, dagmc_filename='dagmc', export_dir='',
        **kwargs
    ):
        """Exports InVesselBuild component STEP files and, optionally, a DAGMC
        neutronics H5M file of in-vessel components via CAD-to-DAGMC.

        Arguments:
            export_cad_to_dagmc (bool): export DAGMC neutronics H5M file of
                in-vessel components via CAD-to-DAGMC (optional, defaults to
                False).
            dagmc_filename (str): name of DAGMC output file, excluding '.h5m'
                extension (optional, defaults to 'dagmc').
            export_dir (str): directory to which to export the output files
                (optional, defaults to empty string).
        """
        self.invessel_build.export_step(export_dir=export_dir)

        if export_cad_to_dagmc:
            self.invessel_build.export_cad_to_dagmc(
                filename=dagmc_filename,
                export_dir=export_dir
            )

    def construct_magnets(
        self, coils_file, cross_section, toroidal_extent, **kwargs
    ):
        """Constructs MagnetSet class object.

        Arguments:
            coils_file (str): path to coil filament data file.
            cross_section (list): coil cross-section definiton. The
                cross-section shape must be either a circle or rectangle. For a
                circular cross-section, the list format is
                ['circle' (str), radius [cm](float)]
                For a rectangular cross-section, the list format is
                ['rectangle' (str), width [cm](float), thickness [cm](float)]
            toroidal_extent (float): toroidal extent to model [deg].

        Optional attributes:
            start_line (int): starting line index for data in filament data file
                (defaults to 3).
            sample_mod (int): sampling modifier for filament points (defaults to
                1). For a user-defined value n, every nth point will be sampled.
            scale (float): a scaling factor between the units of the point-locus
                data and [cm] (defaults to m2cm = 100).
            mat_tag (str): DAGMC material tag to use for magnets in DAGMC
                neutronics model (defaults to 'magnets').
        """
        self.magnet_set = mc.MagnetSet(
            coils_file,
            cross_section,
            toroidal_extent,
            logger=self._logger,
            **kwargs
        )

        self.magnet_set.build_magnet_coils()

    def export_magnets(
        self, step_filename='magnets', export_mesh=False,
        mesh_filename='magnet_mesh', export_dir='', **kwargs
    ):
        """Export magnet components.

        Arguments:
            step_filename (str): name of STEP export output file, excluding
                '.step' extension (optional, optional, defaults to 'magnets').
            export_mesh (bool): flag to indicate tetrahedral mesh generation
                for magnet volumes (optional, defaults to False).
            mesh_filename (str): name of tetrahedral mesh H5M file, excluding
                '.h5m' extension (optional, defaults to 'magnet_mesh').
            export_dir (str): directory to which to export output files
                (optional, defaults to empty string).
        """
        self.magnet_set.export_step(
            filename=step_filename,
            export_dir=export_dir
        )

        if export_mesh:
            self.magnet_set.mesh_magnets()
            self.magnet_set.export_mesh(
                filename=mesh_filename,
                export_dir=export_dir
            )

    def construct_source_mesh(
        self, num_s, num_theta, num_phi, toroidal_extent, **kwargs
    ):
        """Constructs SourceMesh class object.

        Arguments:
            num_s (int) : number of closed flux surfaces for vertex locations in
                each toroidal plane.
            num_theta (int) : number of poloidal angles for vertex locations in
                each toroidal plane.
            num_phi (int) : number of toroidal angles for planes of vertices.
            toroidal_extent (float) : extent of source mesh in toroidal
                direction [deg].

        Optional attributes:
            scale (float): a scaling factor between the units of VMEC and [cm]
                (defaults to m2cm = 100).
        """
        self.source_mesh = sm.SourceMesh(
            self._vmec_obj,
            num_s,
            num_theta,
            num_phi,
            toroidal_extent,
            logger=self._logger,
            **kwargs
        )

        self.source_mesh.create_vertices()
        self.source_mesh.create_mesh()

    def export_source_mesh(
        self, filename='source_mesh', export_dir='', **kwargs
    ):
        """Export source mesh

        Arguments:
            filename (str): name of H5M output file, excluding '.h5m'
                extension (optional, defaults to 'source_mesh').
            export_dir (str): directory to which to export H5M output file
                (optional, defaults to empty string).
        """
        self.source_mesh.export_mesh(
            filename=filename,
            export_dir=export_dir
        )

    def _import_ivb_step(self):
        """Imports STEP files from in-vessel build into Coreform Cubit.
        (Internal function not intended to be called externally)
        """
        for name, data in (
            self.invessel_build.radial_build.radial_build_dict.items()
        ):
            vol_id = cubit_io.import_step_cubit(
                name, self.invessel_build.export_dir
            )
            data['vol_id'] = vol_id

    def _tag_materials_legacy(self):
        """Applies material tags to corresponding CAD volumes for legacy DAGMC
        neutronics model export.
        (Internal function not intended to be called externally)
        """
        if self.magnet_set:
            vol_id_str = " ".join(str(i) for i in list(self.magnet_set.volume_ids))
            cubit.cmd(
                f'group "mat:{self.magnet_set.mat_tag}" add volume {vol_id_str}'
            )

        if self.invessel_build:
            for data in (
                self.invessel_build.radial_build.radial_build_dict.values()
            ):
                cubit.cmd(
                    f'group "mat:{data["mat_tag"]}" add volume {data["vol_id"]}'
                )

    def _tag_materials_native(self):
        """Applies material tags to corresponding CAD volumes for native DAGMC
        neutronics model export.
        (Internal function not intended to be called externally)
        """
        cubit.cmd('set duplicate block elements off')

        if self.magnet_set:
            vol_list = list(self.magnet_set.volume_ids)
            block_id = min(vol_list)
            vol_id_str = " ".join(str(i) for i in vol_list)
            make_material_block(self.magnet_set.mat_tag, block_id, vol_id_str)
        
        if self.invessel_build:
            for data in (
                self.invessel_build.radial_build.radial_build_dict.values()
            ):
                block_id = data['vol_id']
                vol_id_str = str(block_id)
                make_material_block(data['mat_tag'], block_id, vol_id_str)

    def export_dagmc(
        self, skip_imprint=False, legacy_faceting=True, filename='dagmc',
        export_dir='', **kwargs
    ):
        """Exports DAGMC neutronics H5M file of ParaStell components via
        Coreform Cubit.

        Arguments:
            skip_imprint (bool): choose whether to imprint and merge all in
                Coreform Cubit or to merge surfaces based on import order and
                geometry information (optional, defaults to False).
            legacy_faceting (bool): choose legacy or native faceting for DAGMC
                export (optional, defaults to True).
            filename (str): name of DAGMC output file, excluding '.h5m'
                extension (optional, defaults to 'dagmc').
            export_dir (str): directory to which to export DAGMC output file
                (optional, defaults to empty string).

        Optional attributes:
            faceting_tolerance (float): maximum distance a facet may be from
                surface of CAD representation for DAGMC export (defaults to
                None). This attribute is used only for the legacy faceting
                method.
            length_tolerance (float): maximum length of facet edge for DAGMC
                export (defaults to None). This attribute is used only for the
                legacy faceting method.
            normal_tolerance (float): maximum change in angle between normal
                vector of adjacent facets (defaults to None). This attribute is
                used only for the legacy faceting method.
            anisotropic_ratio (float): controls edge length ratio of elements
                (defaults to 100.0). This attribute is used only for the native
                faceting method.
            deviation_angle (float): controls deviation angle of facet from
                surface (i.e., lesser deviation angle results in more elements
                in areas with higher curvature) (defaults to 5.0). This
                attribute is used only for the native faceting method.
        """
        self.faceting_tolerance = None
        self.length_tolerance = None
        self.normal_tolerance = None
        self.anisotropic_ratio = 100.0
        self.deviation_angle = 5.0

        for name, value in kwargs.items():
            self.__setattr__(name, value)

        cubit_io.init_cubit()
        
        self._logger.info(
            'Exporting DAGMC neutronics model...'
        )

        if self.invessel_build:
            self._import_ivb_step()

        if skip_imprint:
            self.invessel_build.merge_layer_surfaces()
        else:
            cubit.cmd('imprint volume all')
            cubit.cmd('merge volume all')

        if legacy_faceting:
            self._tag_materials_legacy()
            cubit_io.export_dagmc_cubit_legacy(
                faceting_tolerance=self.faceting_tolerance,
                length_tolerance=self.length_tolerance,
                normal_tolerance=self.normal_tolerance,
                filename=filename,
                export_dir=export_dir
            )
        else:
            self._tag_materials_native()
            cubit_io.export_dagmc_cubit_native(
                anisotropic_ratio=self.anisotropic_ratio,
                deviation_angle=self.deviation_angle,
                filename=filename,
                export_dir=export_dir
            )


def parse_args():
    """Parser for running as a script.
    """
    parser = argparse.ArgumentParser(prog='stellarator')

    parser.add_argument(
        'filename',
        help='YAML file defining ParaStell stellarator configuration'
    )

    return parser.parse_args()


def read_yaml_config(filename):
    """Read YAML file describing the stellarator configuration and extract all
    data.
    """
    with open(filename) as yaml_file:
        all_data = yaml.safe_load(yaml_file)

    return (
        all_data['vmec_file'], all_data['invessel_build'],
        all_data['magnet_coils'], all_data['source_mesh'],
        all_data['dagmc_export']
    )


def parastell():
    """Main method when run as a command line script.
    """
    args = parse_args()

    (
        vmec_file, invessel_build, magnet_coils, source_mesh, dagmc_export
    ) = read_yaml_config(args.filename)

    stellarator = Stellarator(vmec_file)

    # In-Vessel Build
    stellarator.construct_invessel_build(**invessel_build)
    stellarator.export_invessel_build(**invessel_build)

    # Magnet Coils
    stellarator.construct_magnets(**magnet_coils)
    stellarator.export_magnets(**magnet_coils)

    # Source Mesh
    stellarator.construct_source_mesh(**source_mesh)
    stellarator.export_source_mesh(**source_mesh)
    
    # DAGMC export
    stellarator.export_dagmc(**dagmc_export)


if __name__ == "__main__":
    parastell()
