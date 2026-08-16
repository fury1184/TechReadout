import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()


def create_app():
    app = Flask(__name__)
    
    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL', 
        'mysql+pymysql://techreadout:techreadout@localhost:3306/techreadout'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)

    @app.context_processor
    def inject_app_version():
        from app.version import APP_NAME, APP_VERSION, APP_DISPLAY_VERSION
        return {
            'APP_NAME': APP_NAME,
            'APP_VERSION': APP_VERSION,
            'APP_DISPLAY_VERSION': APP_DISPLAY_VERSION,
        }

    with app.app_context():
        from sqlalchemy import text
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS app_settings (
                `key` VARCHAR(100) PRIMARY KEY,
                `value` VARCHAR(255) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """))
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS lookup_cache (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                cache_key VARCHAR(255) NOT NULL UNIQUE,
                query VARCHAR(255) NOT NULL,
                component_type VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'hit',
                spec_id INTEGER NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX ix_lookup_cache_cache_key (cache_key),
                CONSTRAINT fk_lookup_cache_spec FOREIGN KEY (spec_id) REFERENCES hardware_specs (id)
            )
        """))
        db.session.commit()
    
    # Register blueprints
    from app.routes import main, api, scraper, planner, backup
    app.register_blueprint(main.bp)
    app.register_blueprint(api.bp, url_prefix='/api')
    app.register_blueprint(scraper.bp, url_prefix='/scraper')
    app.register_blueprint(planner.bp, url_prefix='/planner')
    app.register_blueprint(backup.bp, url_prefix='/backup')
    
    return app
