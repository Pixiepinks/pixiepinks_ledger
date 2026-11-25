"""Allow independent customers sharing a display name; preserve every identity."""
from alembic import op
import sqlalchemy as sa
revision='20261125_59'
down_revision='20261125_58'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind)
    constraints=[c for c in inspector.get_unique_constraints('customers') if c['column_names']==['name']]
    indexes=[i for i in inspector.get_indexes('customers') if i.get('unique') and i['column_names']==['name'] and not i.get('duplicates_constraint')]
    if bind.dialect.name=='sqlite' and constraints and bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Legacy SQLite customer-table rebuild requires an offline backup and foreign_keys disabled before the migration transaction; no DDL was executed.')
    if bind.dialect.name=='sqlite' and constraints:
        # Batch reflection names legacy unnamed SQLite constraints explicitly.
        with op.batch_alter_table('customers',naming_convention={'uq':'uq_%(table_name)s_%(column_0_name)s'}) as batch:
            for c in constraints:batch.drop_constraint(c['name'] or 'uq_customers_name',type_='unique')
    else:
        for c in constraints:op.drop_constraint(c['name'],'customers',type_='unique')
    for i in indexes:op.drop_index(i['name'],table_name='customers')
    remaining=sa.inspect(bind).get_indexes('customers')
    if not any(i['column_names']==['name'] and not i.get('unique') for i in remaining):op.create_index('ix_customers_name','customers',['name'],unique=False)


def downgrade():
    # Refuse BEFORE any DDL if legitimate duplicates now exist. Never rename,
    # merge, delete or pick a surviving customer to restore a legacy constraint.
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM customers GROUP BY name HAVING count(*)>1 LIMIT 1')).first():
        raise RuntimeError('Customer-name uniqueness cannot be restored: duplicate names exist. Keep revision 59 and use the documented compatible application rollback; no records were changed.')
    inspector=sa.inspect(bind)
    unique=any(c['column_names']==['name'] for c in inspector.get_unique_constraints('customers')) or any(i.get('unique') and i['column_names']==['name'] for i in inspector.get_indexes('customers'))
    if not unique:
        if bind.dialect.name=='sqlite':
            # Equivalent uniqueness enforcement without rebuilding a referenced table.
            op.create_index('uq_customers_name','customers',['name'],unique=True)
        else:op.create_unique_constraint('uq_customers_name','customers',['name'])
    if 'ix_customers_name' in {i['name'] for i in sa.inspect(bind).get_indexes('customers')}:op.drop_index('ix_customers_name',table_name='customers')
